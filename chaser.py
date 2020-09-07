from selenium import webdriver
import time
import redis
import re
from enum import Enum
import smtplib
import logging
import configparser
import pickle
from core import app, mail
from core.email import send_email
from flask import render_template
import requests

class VOD(Enum):
    IFVOD = 1

class DRAMAOP(Enum):
    CHASE = 1
    ABANDON = 2

class DramaChaser:
    def __init__(self, vod = VOD.IFVOD):
        self.__redis_client = redis.Redis(host='localhost', port=6379, db=0)
        if vod != VOD.IFVOD:
            raise Exception('Other type of VOD is not implemented yet')

    @staticmethod
    def __get_all_users_key():
        return 'users' 

    @staticmethod
    def __get_metadata_key(drama_id):
        return "{}:metadata".format(drama_id)

    def __get_all_users(self):
        return self.__redis_client.smembers(DramaChaser.__get_all_users_key())

    def __get_drama_ids(self, user_id):
        return self.__redis_client.smembers(user_id)

    @staticmethod
    def __get_delta_show_list(current_show_list, old_show_list):
        return [] if old_show_list is None else filter(lambda x: x not in old_show_list, current_show_list)

    def __get_drama_obj(self,drama_id):
        serialized_drama_obj = self.__redis_client.get(drama_id)
        return None if serialized_drama_obj is None else pickle.loads(serialized_drama_obj)

    @staticmethod
    def __get_drama_url(drama_id):
        return "https://www.ifvod.tv/detail?id={}".format(drama_id)

    @staticmethod
    def __get_play_url(play_id):
        return "https://www.ifvod.tv/play?id={}".format(play_id) 

    @staticmethod
    def __parse_ifvod_page(page):
        try:
            match_obj = re.search('<app-media-list.*?>(.*?)</app-media-list>', page)
            return re.findall(r'\"/play\?id=(.*?)\">.*?</a>', match_obj.group(1))
        except Exception as ex:
            logging.error(ex)

    @staticmethod
    def __get_current_show_list(drama_id):
        driver = webdriver.Chrome()
        try:
            driver.get(DramaChaser.__get_drama_url(drama_id))
            # wait to load all pages
            time.sleep(5)
            return DramaChaser.__parse_ifvod_page(driver.page_source)
        finally:            
            driver.close()

    # load drama name from DB, parse webpage if failed
    def load_drama_name(self, drama_id):
        metadata_key = DramaChaser.__get_metadata_key(drama_id)
        response = self.__redis_client.get(metadata_key)
        if response is not None:
            return pickle.loads(response)['drama_name']
        url = DramaChaser.__get_drama_url(drama_id)
        page = requests.get(url)
        drama_name = DramaChaser.__parse_metadata_page(page.text)
        payload = {}
        payload['drama_name'] = drama_name
        self.__redis_client.set(metadata_key, pickle.dumps(payload))
        return drama_name

    @staticmethod
    def __parse_metadata_page(page):
        match_obj = re.search('<meta.*?name="title".*?content="(.*?) - IFVOD".*?/>', page)
        return match_obj.group(1)

    # key => drama_id
    # value => dict {
    #   last_updated_time:<last_updated_time>, 
    #   current_show_list:<current_show_list>, 
    #   delta_show_list:<delta_show_list>
    # }
    def __get_drama_updates(self, drama_id):
        old_obj = self.__get_drama_obj(drama_id)
        if old_obj is not None and time.time() - old_obj['last_updated_time'] <= 3600:
            return old_obj['delta_show_list']
        current_show_list = DramaChaser.__get_current_show_list(drama_id)
        old_show_list = old_obj['current_show_list'] if old_obj is not None else None
        delta_show_list = DramaChaser.__get_delta_show_list(current_show_list, old_show_list)
        obj = {}
        obj['last_updated_time'] = time.time()
        obj['current_show_list'] = current_show_list
        obj['delta_show_list'] = delta_show_list
        self.__redis_client.set(drama_id, pickle.dumps(obj))
        return delta_show_list

    def __get_all_drama_reports(self, user_id):
        drama_ids = self.__get_drama_ids(user_id)
        reports = {}
        if not isinstance(drama_ids, set):
            return reports
    
        for drama_id in drama_ids:
            updates = self.__get_drama_updates(drama_id)
            if len(updates) != 0:
                reports[drama_id] = updates
        return reports

    def __notify_user_by_email(self, email, reports):
        if len(reports) == 0:
            return
        with app.app_context():
            send_email(
                '[DramaChaser] Drama updates',
                sender=app.config['ADMINS'][0], 
                recipients=[email],
                text_body=render_template('email/drama_updates.txt', reports=reports),
                html_body=render_template('email/drama_updates.html', reports=reports)
            ) 
            
    def __update_drama(self, user_id, op, drama_id):
        pipe = self.__redis_client.pipeline()
        while True:
            try:
                pipe.watch(user_id)
                pipe.multi()
                if op == DRAMAOP.CHASE:
                    pipe.sadd(user_id, drama_id) # user to drama mapping
                    pipe.sadd(DramaChaser.__get_all_users_key(), user_id) # all users mapping
                else:
                    pipe.srem(user_id, drama_id)
                    pipe.srem(DramaChaser.__get_all_users_key(), user_id)
                pipe.execute()
                break
            except redis.WatchError:
                continue
            finally:
                pipe.reset()
    
    # chase a drama from UI
    def chase(self, user_id, drama_id, drama_name):
        self.__update_drama(user_id, DRAMAOP.CHASE, drama_id)
    
    # abandon a drama from UI
    def abandon(self, user_id, drama_id):
        self.__update_drama(user_id, DRAMAOP.ABANDON, drama_id)

    # complete drama information in cron job
    def scheduled_chase(self):
        all_users = self.__get_all_users()
        if not isinstance(all_users, set):
            logging.info('No user chase drama, exit')
            return
        for user in all_users:
            reports = self.__get_all_drama_reports(user)
            self.__notify_user_by_email(user, reports)

    @staticmethod
    def __transform_showlist_to_urls(show_list):
        if show_list is None:
            return None
        return [DramaChaser.__get_play_url(show) for show in show_list]

    @staticmethod
    def __get_show_list(drama_obj):
        return None if drama_obj is None else drama_obj['current_show_list']

    # get all drama metadata for a user
    def get_drama_metadata(self, user_id):
        drama_ids = list(self.__get_drama_ids(user_id))
        drama_metadata = {}
        for drama_id in drama_ids:
            payload = {}
            drama_obj = self.__get_drama_obj(drama_id)
            payload['show_list'] = DramaChaser.__transform_showlist_to_urls(DramaChaser.__get_show_list(drama_obj))
            payload['drama_name'] = self.load_drama_name(drama_id)
            drama_metadata[self.__get_drama_url(drama_id)] = payload
        return drama_metadata
    
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    drama_chaser = DramaChaser(vod=VOD.IFVOD)
    drama_chaser.scheduled_chase()
