import os
import stat
import sys
import time
from datetime import datetime

import telethon.events
from telethon import TelegramClient, events, Button, types
from telethon.sessions import SQLiteSession
from pymongo import MongoClient
import asyncio
import logging
import re
import shutil
import psutil
import json
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.errors.rpcerrorlist import PhoneNumberInvalidError, FloodTestPhoneWaitError, FloodWaitError
from telethon.errors.rpcerrorlist import ChannelPrivateError,ChatForbiddenError,ChatWriteForbiddenError
from telethon.errors.rpcerrorlist import UserBannedInChannelError
from telethon.errors import SessionPasswordNeededError

# TODO:
# Ensure it filtered base on sending data
# Create timer for gap between messages
# Ensure proper deletion of all files when doing so


# TODO:
# TODO.1: Mahince state tracker for conv
logging.basicConfig(filename='general_logger',
                    filemode='a',
                    format='[%(levelname) 5s/%(asctime)s] %(name)s: %(message)s',
                    level=logging.WARNING)
custom_logger = open('internal_errors', mode='a')
distributor_debug_flag = True
listener_debug_flag = True
if distributor_debug_flag == True:
    distributors_errors = open('distributors_errors', mode='a')
print('initiating Database')
db_conn = MongoClient("mongodb://localhost:27017/")

# Constants
MAINTENANCE = True
DB_NAME = 'selfAdv'
ADMINS = db_conn[DB_NAME]['Admins']
GROUPS = db_conn[DB_NAME]['Groups']
ADS = db_conn[DB_NAME]['Advertisements']
BOTS = db_conn[DB_NAME]['Bots']
GROUPS_DATA = 'future container'
GROUPS_ENTITY = 'future container'
ADS_DATA = 'future container'
POST_INTERVAL = 4.5
POST_AGGREGATOR_STATE = False
DISTRIBUTORS = None
NOTIFICATION_USER = ADMINS.find_one({"notification": "True"})['username']

'''
Rate Limiter:
if someone wants to upgrade the following values most change:
PPM and PPM_RELOAD to the same value
'''
PPM = 800
PPM_RELOAD = 800
PPM_TIMER = None

api_info = list(BOTS.find({"api_id": {"$exists": True}}, {"_id": 0, "api_id": 1, "api_hash": 1}))
ADMIN_INTERFACE = [
    [Button.inline(text='העלאת מודעה', data='upload_adv_dash'),
     Button.inline(text='מחיקת מודעה', data='delete_adv_dash')],
    [Button.inline(text='הוספת קבוצה', data='add_channel_dash'),
     Button.inline(text='מחיקת קבוצה', data='del_channel_dash')],
    [Button.inline(text='הוספת משתמש הפצה', data='add_user_distributors_dash'),
     Button.inline(text='מחיקת משתמש הפצה', data='del_user_distributors_dash')],
    [Button.inline(text='הפסקת עבודה', data='MAINTENANCE_ENABLE'),
     Button.inline(text='התחלת עבודה', data='MAINTENANCE_DISABLE')],
    [Button.inline(text='עדכון קצב פרסומים (בשניות)',data='POST_INTERVAL')],
    [Button.inline(text='עדכון ערך PPM',data='PPM_LIMITER')],
    [Button.inline(text='בדוק תקינות משתמשים',data='TEST_CLIENTS')]
]
notification_button = [[Button.inline(text='אשר קבלה', data='accept_manager_notification')]]

print('initiating bot')
# managers
bot_manager_cred = list(BOTS.find({"role": "manager"}, {"_id": 0, "role": 1, "token": 1}))
bot_manager = TelegramClient(session='manager', api_id=api_info[0]["api_id"], api_hash=api_info[0]["api_hash"]).start(
    bot_token=bot_manager_cred[0]["token"])

# workers
class User_sessions:
    def __init__(self):
        self.sessions = {}

    async def add_session(self, username, session):
        self.sessions[username] = session

    async def get_session(self, username):
        if username in self.sessions.keys():
            return self.sessions[username]
        return None
class User:
    # TODO: may require to combine chat_id with msg_id to achieve uniqueness
    def __init__(self, username, main_msg=None, buttons=None, text=None):
        self.main_msg = main_msg
        self.buttons = [buttons]
        self.text = [text]
        self.func = None
        self.browse = False
        self.lock = False
        self.username = username

    async def ADD_TO_SESSIONS(self, active_sessions):
        await active_sessions.add_session(self.username, self)

    async def add_msg(self, buttons=None, text=None):
        self.buttons.append(buttons)
        self.text.append(text)

    async def previous_dashboard(self):
        self.buttons.pop()
        self.text.pop()
        return (self.text[-1], self.buttons[-1])

    async def current_dashboard(self):
        return (self.text[-1], self.buttons[-1])

    async def browse_enable(self):
        if self.browse:
            return True
        self.browse = True
        return False

    async def browse_disable(self):
        self.browse = False
        self.adv_menu = None
        self.total_adv = None
        self.position = None

    async def browse_check(self):
        if self.browse:
            return True
        return False

    async def browse_adv(self, adv_menu):
        self.adv_menu = adv_menu
        self.total_adv = len(self.adv_menu) - 1
        self.position = 0
        return self.adv_menu[self.position]

    async def browse_singelton(self):
        if self.total_adv == 0:
            return True
        return False

    async def next_browse_adv(self):
        if self.position < self.total_adv:
            self.position += 1
        else:
            self.position = 0
        return self.adv_menu[self.position]

    async def previous_browse_adv(self):
        if 0 < self.position <= self.total_adv:
            self.position -= 1
        elif self.position == 0:
            self.position = self.total_adv
        return self.adv_menu[self.position]

    async def current_browse_adv(self):
        return self.adv_menu[self.position]

    async def remove_browse_adv(self):
        self.adv_menu.pop(self.position)
        self.total_adv -= 1
        self.position -= 1
        if self.total_adv == -1:
            self.browse = False
            return False
        return True
class Distributor:
    def __init__(self, phone_number=None, api_id=None, api_hash=None, bot_manager=None):
        self.api_id = api_id
        self.api_hash = api_hash
        self.name = None
        self.phone_number = phone_number
        self.folder = f'{os.getcwd()}/bot_database/client_info/distributors/'
        self.role = 'distributors'
        self.bot_manager = bot_manager
        self.notification_button = [[Button.inline(text='אשר קבלה', data='accept_manager_notification')]]
    #Mechanism
    async def code_listener_send(self, aggregator):
        try:
            self.session_file = SQLiteSession(self.folder + self.phone_number)
            self.client = TelegramClient(session=self.session_file,
                                         api_id=self.api_id,
                                         api_hash=self.api_hash)
            await self.client.connect()
            if not await self.client.is_user_authorized():
                await self.client.send_code_request(phone=self.phone_number, force_sms=True)
            return True
        except PhoneNumberInvalidError as ex:
            if distributor_debug_flag:
                distributors_errors.flush()
                distributors_errors.write(f'[{datetime.now()}] - The following error occur while code_listener_send: '
                                          f'caused by PhoneNumberInvalidError: {str(ex)}\n')
                distributors_errors.flush()
            await aggregator.send_message(message='המספר שהוכנס אינו תקין, אנא וודא שאין רווחים',
                                          buttons=self.notification_button)
            await self.client.disconnect()
            self.session_file.close()
            self.session_file.delete()
            return False
        except (FloodTestPhoneWaitError, FloodWaitError) as ex:
            if distributor_debug_flag:
                distributors_errors.flush()
                distributors_errors.write(f'[{datetime.now()}] - The following error occur while code_listener_send: '
                                          f'caused by FloodTestPhoneWaitError,FloodWaitError: {str(ex)}\n')
                distributors_errors.flush()
            await aggregator.send_message(message='המספר בהשהייה על ידי טלגרם עקב נסיונות מרובים',
                                          buttons=self.notification_button)
            await self.client.disconnect()
            self.session_file.close()
            self.session_file.delete()
            return False
        except Exception as ex:
            distributors_errors.flush()
            custom_logger.write(f'[{datetime.now()}] - the following error occur while code_listener_send: {str(ex)}\n')
            distributors_errors.flush()
            self.session_file.close()
            self.session_file.delete()
            return False
    async def insert_distributor(self, code):
        try:
            await self.client.sign_in(phone=self.phone_number, code=code)
            client_info = await self.client.get_me()
            if client_info is not None:
                BOTS.insert_one({"phone": self.phone_number,
                                 "session": f"{self.folder}{self.phone_number}"
                                            f".session",
                                 "username": client_info.username,
                                 "role": self.role})
                await self.client.disconnect()
                self.session_file.close()
                return True
        except SessionPasswordNeededError as ex:
            return 'SessionPasswordNeededError'
        except Exception as ex:
            self.session_file.close()
            self.session_file.delete()
            return False
    async def insert_distributor_2fa(self,code):
        await self.client.sign_in(phone=self.phone_number, password=code)
        client_info = await self.client.get_me()
        if client_info is not None:
            BOTS.insert_one({"phone": self.phone_number,
                             "session": f"{self.folder}{self.phone_number}"
                                           f".session",
                             "username": client_info.username,
                             "role": self.role})
            await self.client.disconnect()
            self.session_file.close()
            return True
    async def active_client(self):
        self.session_file = SQLiteSession(self.folder + self.phone_number)
        try:
            self.client = TelegramClient(session=self.session_file,
                                         api_id=self.api_id,
                                         api_hash=self.api_hash)
            await self.client.connect()
            await self.client.get_me()

        except Exception as ex:
            distributors_errors.flush()
            custom_logger.write(f'[{datetime.now()}] - error occur while load_from_disk: {str(ex)}\n')
            distributors_errors.flush()
    async def deactive_client(self):
        global POST_AGGREGATOR_STATE
        while True:
            if POST_AGGREGATOR_STATE == False:
                await self.client.disconnect()
                break
            else:
                await asyncio.sleep(1)
        self.session_file.close()

    async def delete_client(self):
        self.session_file = SQLiteSession(self.folder + self.phone_number)
        try:
            self.client = TelegramClient(session=self.session_file,
                                         api_id=self.api_id,
                                         api_hash=self.api_hash)
            await self.client.connect()
            await self.client.get_me()
            await self.client.log_out()
            BOTS.delete_one({"phone": f"{self.phone_number}"})
        except Exception as ex:
            custom_logger.flush()
            custom_logger.write(f'[{datetime.now()}] - error occur while delete_client distributor: {str(ex)}\n')
            custom_logger.flush()
    #Work
    async def get_name(self):
        return self.phone_number
    async def send_file(self,entity,photo,text):
        try:
            await self.client.send_file(entity=entity.username,
                                  file=photo,
                                  caption=text)
            return {'client':self.phone_number}


        except ChatForbiddenError as ex:
            while True:
                try:
                    sender  = await self.client.get_me()
                    await self.bot_manager.send_message(entity=NOTIFICATION_USER,
                                                        message='המפיץ הבא'
                                                                '\n '
                                                        f' @{sender.username} '
                                                                f'\n'
                                                        f'לא מצליח לשלוח הודעות לקבוצה '
                                                                f'\n'
                                                        f' @{entity.username} '
                                                        f'\n'
                                                        f'עקב התקלה הבאה: ChatForbiddenError ',
                                                        buttons=notification_button)
                    return {'client':self.phone_number,'error':ChatForbiddenError}
                except Exception as ex:
                    if distributor_debug_flag:
                        distributors_errors.flush()
                        distributors_errors.write(f'[{datetime.now()}] -  The following error occur while send_post in Distributors: '
                                                  f'for {sender.username} in {entity.username}'
                                                  f' caused by ChatForbiddenError: '
                                                  f'{str(ex)}\n')
                        distributors_errors.flush()

        except ChannelPrivateError as ex:
            while True:
                try:
                    sender  = await self.client.get_me()
                    await self.bot_manager.send_message(entity=NOTIFICATION_USER,
                                                        message='המפיץ הבא '
                                                                '\n'
                                                        f' @{sender.username}'
                                                                f'\n '
                                                        f'לא מצליח לשלוח הודעות לקבוצה '
                                                                f'\n'
                                                        f' @{entity.username} '
                                                        f'\n'
                                                        f'עקב התקלה הבאה: ChatChannelPrivateError ',
                                                        buttons=notification_button)
                    return {'client':self.phone_number,'error':ChannelPrivateError}
                except Exception as ex:
                    if distributor_debug_flag:
                        distributors_errors.flush()
                        distributors_errors.write(f'[{datetime.now()}] - The following error occur while send_post in Distributors: '
                                                  f'for {sender.username} in {entity.username}'
                                                  f' caused by ChannelPrivateError: '
                                                  f'{str(ex)}\n')
                        distributors_errors.flush()

        except ChatWriteForbiddenError as ex:
            while True:
                try:
                    sender  = await self.client.get_me()
                    await self.bot_manager.send_message(entity=NOTIFICATION_USER,
                                                        message='המפיץ הבא '
                                                                '\n'
                                                        f' @{sender.username}'
                                                                f'\n '
                                                        f'לא מצליח לשלוח הודעות לקבוצה '
                                                                f'\n'
                                                        f' @{entity.username} '
                                                        f'\n'
                                                        f'עקב התקלה הבאה: ChatWriteForbiddenError ',
                                                        buttons=notification_button)
                    return {'client':self.phone_number,'error':ChannelPrivateError}
                except Exception as ex:
                    if distributor_debug_flag:
                        distributors_errors.flush()
                        distributors_errors.write(f'[{datetime.now()}] - The following error occur while send_post in Distributors: '
                                                  f'for {sender.username} in {entity.username}'
                                                  f' caused by ChatWriteForbiddenError: '
                                                  f'{str(ex)}')
                        distributors_errors.flush()

        except UserBannedInChannelError as ex:
            while True:
                try:
                    sender  = await self.client.get_me()
                    await self.bot_manager.send_message(entity=NOTIFICATION_USER,
                                                        message='המפיץ הבא '
                                                                '\n'
                                                        f' @{sender.username}'
                                                                f'\n '
                                                        f'לא מצליח לשלוח הודעות לקבוצה '
                                                                f'\n'
                                                        f' @{entity.username} '
                                                        f'\n'
                                                        f'עקב התקלה הבאה: UserBannedInChannelError',
                                                        buttons=notification_button)
                    return {'client':self.phone_number,'error':UserBannedInChannelError}
                except Exception as ex:
                    if distributor_debug_flag:
                        distributors_errors.flush()
                        distributors_errors.write(f'[{datetime.now()}] - The following error occur while send_post in Distributors: '
                                                  f'for {sender.username} in {entity.username}'
                                                  f' caused by ChatWriteForbiddenError: '
                                                  f'{str(ex)}')
                        distributors_errors.flush()


        except Exception as ex:
            if distributor_debug_flag:
                sender = await self.client.get_me()
                distributors_errors.flush()
                distributors_errors.write(f'[{datetime.now()}] - The following error occur while send_post in Distributors: '
                                          f'for {sender.username} in {entity.username}'
                                          f' caused by send_file: '
                                          f'{str(ex)}\n')
                distributors_errors.flush()
            return {'client':self.phone_number,'error':'Unknown Error'}
    async def send_message(self,entity,message):
        try:
            await self.client.send_message(entity=entity,
                                           message=message)
        except Exception as ex:
            print(str(ex))


    async def get_username(self):
        try:
            user_info = await self.client.get_me()
            return user_info.username
        except Exception as ex:
            custom_logger(f'[{datetime.now()}] - the following error occur in get_username inside Distributor class: '
                          f'{str(ex)}\n')
    async def get_number(self):
        return self.phone_number
class DistributorManager:
    def __init__(self,api_id, api_hash, bot_manager):
        self.api_id = api_id
        self.api_hash = api_hash
        self.bot_manager = bot_manager
        self.distributors_clients = []
        self.total = len(self.distributors_clients) - 1
        self.cursor = -1
        self.posts = []
        self.post_cursor = 0
        self.folder = f'{os.getcwd()}/bot_database/client_info/distributors/'
    async def active_distributors_for_test(self):
        for distributor in os.listdir(self.folder):
            try:
                tmp = Distributor(phone_number=distributor[:-8],
                                  api_id=api_info[0]['api_id'],
                                  api_hash=api_info[0]['api_hash'],
                                  bot_manager=bot_manager)
                await tmp.active_client()
                self.distributors_clients.append(tmp)
                self.total += 1
            except Exception as ex:
                custom_logger(f'[{datetime.now()}] - the following error occur in DistributorManager, '
                              f'active_distributrs: {str(ex)}\n')

    async def active_distributors(self,posts):
        self.posts = posts
        for distributor in os.listdir(self.folder):
            try:
                tmp = Distributor(phone_number=distributor[:-8],
                                  api_id=api_info[0]['api_id'],
                                  api_hash=api_info[0]['api_hash'],
                                  bot_manager=bot_manager)
                await tmp.active_client()
                self.distributors_clients.append(tmp)
                self.total += 1
            except Exception as ex:
                custom_logger(f'[{datetime.now()}] - the following error occur in DistributorManager, '
                              f'active_distributrs: {str(ex)}\n')
    async def deactive_distributors(self):
        for distributor in self.distributors_clients:
            await distributor.deactive_client()
    async def _next_client(self):
        if self.cursor < self.total:
            self.cursor += 1
        else:
            self.cursor = 0
        return self.cursor

    async def ppm_adjustor(self):
        global PPM
        PPM -= len(self.posts)

    async def post_rotator(self,cycle):
        if cycle == 0:
            while True:
                tmp_post_storage = self.posts
                self.posts = []
                non_rotational = []

                for i in range(1, len(tmp_post_storage) + 1):
                    if i == len(tmp_post_storage):
                        if tmp_post_storage[0]['rotation'] == False:
                            non_rotational.append({'post': tmp_post_storage[0], 'index': 0})
                            continue
                        self.posts.append(tmp_post_storage[0])
                    else:
                        if tmp_post_storage[i]['rotation'] == False:
                            non_rotational.append({'post': tmp_post_storage[i], 'index': i})
                            continue
                        self.posts.append(tmp_post_storage[i])

                for i in non_rotational:
                    self.posts.insert(i['index'],i['post'])
                break
        else:
            pass

    async def post_processor(self,cycle):
        await self.ppm_adjustor()
        await self.post_rotator(cycle)

        return self.posts


    async def send_post(self,entity,cycle):
        global MAINTENANCE
        for post in await self.post_processor(cycle):
            try:
                tmp = await self.distributors_clients[await self._next_client()].send_file(
                    entity=entity,
                    photo=post['photo_url'],
                    text=post['text'])

            except (ChannelPrivateError,ChatForbiddenError,ChatWriteForbiddenError,TypeError) as ex:
                if distributor_debug_flag == True:
                    distributors_errors.flush()
                    distributors_errors.write(f'[{datetime.now()}] - the following error occur while send_post inside DistributorManager: '
                                              f'{str(ex)}\n')
                    distributors_errors.flush()
                return
            except UserBannedInChannelError as ex:
                if distributor_debug_flag == True:
                    distributors_errors.flush()
                    distributors_errors.write(f'[{datetime.now()}] - the following error occur while send_post inside DistributorManager: '
                                              f'{str(ex)}\n')
                    distributors_errors.flush()
                return

            except Exception as ex:
                if distributor_debug_flag == True:
                    distributors_errors.flush()
                    distributors_errors.write(f'[{datetime.now()}] - the following error occur while send_post inside DistributorManager: '
                                              f'{str(ex)}\n')
                    distributors_errors.flush()
    async def send_post_test(self,entity):
        working_clients = []
        non_working_clients = []

        for index,client in enumerate(self.distributors_clients):
            try:
                await client.send_message(entity=entity,
                                               message=f'i am a working client numbered as {index}')
                working_clients.append(f'{await client.get_username()} : {await client.get_number()}')
            except Exception as ex:
                distributors_errors.flush()
                distributors_errors.write(
                    f'[{datetime.now()}] - the following error occur while send_post_test inside DistributorManager: '
                    f'{str(ex)}\n')
                distributors_errors.flush()
                non_working_clients.append(f'{await client.get_username()} : {await client.get_number()}')
        return working_clients,non_working_clients
class GroupTimer:
    def __init__(self,name):
        self.name = name
        self.suspend = time.time()
    async def can_i_send(self):
        if self.suspend > time.time():
            return False
        self.suspend = time.time()
        return True
    async def get_name(self):
        return self.name
class TimeManager:
    def __init__(self):
        self.groups = []
    async def add_group(self,group):
        self.groups.append(group)
    async def check_status_for(self,name):
        for group in self.groups:
            if await group.get_name() == name:
                return await group.can_i_send()
    async def check_existence(self,name):
        if len(self.groups) != 0:
            for group in self.groups:
                if await group.get_name() == name:
                    return True
        return False



user_sessions = User_sessions()
time_manager = TimeManager()
# Manager
@bot_manager.on(events.NewMessage(pattern='/start'))
async def start(update):
    user_info = await update.get_sender()
    user_record = ADMINS.find_one({'username': user_info.username})
    if user_record:
        async with bot_manager.conversation(entity=update.chat, timeout=5) as auth:
            msg = await auth.send_message('אנא הכנס סיסמה')
            try:
                admin_pass = await auth.get_response()
                if admin_pass.message == user_record['password']:
                    await bot_manager.delete_messages(entity=user_record['username'],
                                                      message_ids=[msg, admin_pass, update.id])
                    try:  # in case of attempt to initiate user interface a second time it delete the first one
                        username = user_info.username
                        user_session = await user_sessions.get_session(username)
                        await bot_manager.delete_messages(entity=username,
                                                          message_ids=user_session.main_msg)
                    except Exception as ex:
                        pass

                    finally:
                        main_msg = await auth.send_message(message='הוראות ממשק:',
                                                           buttons=ADMIN_INTERFACE,
                                                           file='bot_database/botphoto/manager/manager.jpg')
                        tmp = User(user_info.username, main_msg.id, buttons=ADMIN_INTERFACE, text='הוראות ממשק:')
                        await tmp.ADD_TO_SESSIONS(user_sessions)
            except asyncio.TimeoutError:
                await auth.send_message('אימות נכשל,התחל מחדש')

    raise events.StopPropagation
@bot_manager.on(events.CallbackQuery)
async def guard(update):  # General function to avoid stall sessions
    user_info = await update.get_sender()
    username = user_info.username
    tmp = await user_sessions.get_session(username)
    if ADMINS.find({'username': user_info.username}) != None:
        if tmp != None:
            pass
        else:
            await update.answer(alert=True, message='אנא התאמת מחדש על ידי שליחת start/')
            await update.delete()
            raise events.StopPropagation
    else:
        raise events.StopPropagation
@bot_manager.on(events.CallbackQuery(pattern='accept_manager_notification'))
async def accept_manager_notification(update):
    await update.delete()
    raise events.StopPropagation
@bot_manager.on(events.CallbackQuery(pattern='step_back'))
async def previous_dashboard(update):
    user_info = await update.get_sender()
    username = user_info.username
    user_session = await user_sessions.get_session(username)

    dash = await user_session.previous_dashboard()
    while True:
        try:
            await update.edit(text=dash[0], buttons=dash[1])
            break
        except Exception as ex:
            custom_logger.flush()
            custom_logger.write(f"caused by previous_dashboard: {str(ex)}\n")
            custom_logger.flush()
    raise events.StopPropagation
@bot_manager.on(events.CallbackQuery(pattern='POST_INTERVAL'))
async def post_interval(update):
    global POST_INTERVAL
    global MAINTENANCE

    user_info = await update.get_sender()
    username = user_info.username
    user_session = await user_sessions.get_session(username)

    if MAINTENANCE == False:
        while True:
            try:
                await update.answer(alert=True, message='ניתן לביצוע רק כאשר הבוט מכובה')
                return
            except Exception as ex:
                pass

    try:
        async with bot_manager.conversation(entity=update.chat, timeout=60) as aggregator:
            msg_req = await aggregator.send_message('אנא שלח ערך עבור קצב פרסום בשניות')
            msg_reply = await aggregator.get_response()
            await bot_manager.delete_messages(entity=username,
                                              message_ids=[msg_req,msg_reply])
            POST_INTERVAL = float(msg_reply.message)
            msg = await aggregator.send_message('הזמן עודכן בהצלחה',buttons=notification_button)
            await update.answer()
            aggregator.cancel()

    except Exception as ex:
        await bot_manager.send_message(entity=NOTIFICATION_USER,
                                       message=f'{str(ex)}התקלה הבאה התרחשה בעת הפעולה: ',
                                       buttons=notification_button)
    raise events.StopPropagation
@bot_manager.on(events.CallbackQuery(pattern='PPM_LIMITER'))
async def ppm_limiter(update):
    global PPM
    global PPM_RELOAD
    global MAINTENANCE

    user_info = await update.get_sender()
    username = user_info.username
    user_session = await user_sessions.get_session(username)

    if MAINTENANCE == False:
        while True:
            try:
                await update.answer(alert=True, message='ניתן לביצוע רק כאשר הבוט מכובה')
                return
            except Exception as ex:
                pass

    try:
        async with bot_manager.conversation(entity=update.chat, timeout=60) as aggregator:
            msg_req = await aggregator.send_message('אנא שלח ערך PPM')
            msg_reply = await aggregator.get_response()
            await bot_manager.delete_messages(entity=username,
                                              message_ids=[msg_req,msg_reply])
            PPM_RELOAD = float(msg_reply.message)
            PPM = PPM_RELOAD
            msg = await aggregator.send_message('הזמן עודכן בהצלחה',buttons=notification_button)
            await update.answer()
            aggregator.cancel()
    except Exception as ex:
        await bot_manager.send_message(entity=NOTIFICATION_USER,
                                       message=f'{str(ex)}התקלה הבאה התרחשה בעת הפעולה: ',
                                       buttons=notification_button)
    raise events.StopPropagation
@bot_manager.on(events.CallbackQuery(pattern='TEST_CLIENTS'))
async def test_clients(update):
    global MAINTENANCE
    global DISTRIBUTORS

    user_info = await update.get_sender()
    username = user_info.username
    user_session = await user_sessions.get_session(username)

    if MAINTENANCE == False:
        while True:
            try:
                await update.answer(alert=True, message='ניתן לביצוע רק כאשר הבוט מכובה')
                return
            except Exception as ex:
                pass
    await update.answer()

    DISTRIBUTORS = DistributorManager(api_id=api_info[0]['api_id'],
                                      api_hash=api_info[0]['api_hash'],
                                      bot_manager=bot_manager)
    try:
        sender = await update.get_sender()
        await DISTRIBUTORS.active_distributors_for_test()
        async with bot_manager.conversation(entity=update.chat, timeout=60) as aggregator:
            request = await aggregator.send_message('שלח קבוצה או משתמש קצה כדי לשלוח אליו הודעות שפיות')
            dst_test_grp = await aggregator.get_response()
            await bot_manager.delete_messages(entity=username,message_ids=[request, dst_test_grp])
            aggregator.cancel()
        try:
            client_succesful,client_unsuccesful = await DISTRIBUTORS.send_post_test(dst_test_grp.text)
            client_unsuccesful_str = ''
            client_succesful_str = ''
            for i in client_unsuccesful:
                client_unsuccesful_str += f'{i}' + '\n'
            for i in client_succesful:
                client_succesful_str += f'{i}' + '\n'
            await bot_manager.send_message(entity=sender.username,
                                           message=f'המשתמשים הבאים נכשלו:'
                                                   f'\n'
                                                   f'{client_unsuccesful_str}'
                                                   f'\n'
                                                   f'המשתמשים הבאים הצליחו:'
                                                   f'\n'
                                                   f'{client_succesful_str}',
                                           buttons=notification_button)
        except Exception as ex:
            custom_logger.flush()
            custom_logger.write(f"[{datetime.now()}] - the following error occur while test_clients: {str(ex)} \n")
    except Exception as ex:
        custom_logger.flush()
        custom_logger.write(f"[{datetime.now()}] - the following error occur while test_clients: {str(ex)} \n")
    await DISTRIBUTORS.deactive_distributors()

    return events.StopPropagation

@bot_manager.on(events.CallbackQuery(pattern=r'.*upload_adv_dash.*'))
async def upload_adv_dashboard(update):
    user_info = await update.get_sender()
    username = user_info.username
    user_session = await user_sessions.get_session(username)

    if update.data.decode() == 'upload_adv_dash':
        upload_dash = [
            [Button.inline(text='הכנס מודעה בשלבים', data='parts_upload_adv_dash')],
            [Button.inline(text='חזור', data='step_back')]
        ]
        await update.edit(buttons=upload_dash)
        await user_session.add_msg(buttons=upload_dash)
    else:
        try:
            next_post_num = ADS.find({}, {"_id": 0, "post_num": 1}).sort("post_num", -1).limit(1).next()['post_num'] + 1
            post_name = f'adv_{next_post_num}'
            default_post_position = int(post_name[-1])
        except StopIteration:
            next_post_num = 1
            post_name = f'adv_{next_post_num}'

        async with bot_manager.conversation(entity=update.chat, timeout=60) as aggregator:
            try:
                if update.data.decode() == 'parts_upload_adv_dash':
                    await update.answer()
                    while True:
                        request = await aggregator.send_message('שלח קישור הורדה ישיר לתמונה (אין מנגנון מניעת טעויות)')
                        photo = await aggregator.get_response()
                        await bot_manager.delete_messages(entity=username,
                                                          message_ids=[request, photo])
                        break
                    while True:
                        request = await aggregator.send_message('שלח כיתוב')
                        adv_text = await aggregator.get_response()
                        await bot_manager.delete_messages(entity=username,
                                                          message_ids=[request, adv_text])
                        if adv_text.text is not None:
                            break
                        else:
                            continue

                    if adv_text.text:
                        ADS.insert_one({'name': post_name,
                                        'photo': 'None',
                                        'photo_url':photo.text,
                                        'text': adv_text.text,
                                        'type':'link',
                                        'post_position':0,
                                        'rotation':True,
                                        'post_num': next_post_num,
                                        'status': 'לא פעילה'})
                    else:
                        await aggregator.send_message('אחד הנתונים שהוזנו אינו תקין. נסה שנית:'
                                                      'שים לב כי שמות משתמש צריכים להתחיל ב @')
            except asyncio.TimeoutError:
                await aggregator.send_message('עבר הזמן התחל מחדש')

    raise events.StopPropagation
@bot_manager.on(events.CallbackQuery(pattern=r'delete_adv.*'))
async def delete_adv_dashboard(update):
    global MAINTENANCE

    user_info = await update.get_sender()
    username = user_info.username
    user_session = await user_sessions.get_session(username)

    async def sender_helper(adv):
        if adv['status'] == 'לא פעילה' and adv['rotation'] == True:
            ADV_INTERFACE.append(ADV_INTERFACE_ACTIVE_BUTTON)
            ADV_INTERFACE.append(ADV_INTERFACE_ROTATION_INACTIVE)
            ADV_INTERFACE.append(ADV_INTERFACE_BACK_BUTTON)

        elif adv['status'] == 'לא פעילה' and adv['rotation'] == False:
            ADV_INTERFACE.append(ADV_INTERFACE_ACTIVE_BUTTON)
            ADV_INTERFACE.append(ADV_INTERFACE_ROTATION_ACTIVE)
            ADV_INTERFACE.append(ADV_INTERFACE_BACK_BUTTON)

        elif adv['status'] == 'פעילה' and adv['rotation'] == True:
            ADV_INTERFACE.append(ADV_INTERFACE_INACTIVE_BUTTON)
            ADV_INTERFACE.append(ADV_INTERFACE_ROTATION_INACTIVE)
            ADV_INTERFACE.append(ADV_INTERFACE_BACK_BUTTON)

        elif adv['status'] == 'פעילה' and adv['rotation'] == False:
            ADV_INTERFACE.append(ADV_INTERFACE_INACTIVE_BUTTON)
            ADV_INTERFACE.append(ADV_INTERFACE_ROTATION_ACTIVE)
            ADV_INTERFACE.append(ADV_INTERFACE_BACK_BUTTON)

        while True:
            try:
                await update.edit(file=adv['photo_url'],
                                  text=f"{adv['text']}\n\n"
                                       f"📨📨️ סטטוס מודעה: "
                                       f"{adv['status']}\n"
                                       f"סדר מודעה: "
                                       f"{adv['post_position']}\n",
                                  buttons=ADV_INTERFACE)
                break
            except Exception as ex:
                custom_logger.flush()
                custom_logger.write(str(ex) + '\n')
                custom_logger.flush()

    ADV_INTERFACE = [
        [Button.inline(text='הבא', data='delete_adv_next_adv'),
         Button.inline(text='הקודם', data='delete_adv_previous_adv')],
        [Button.inline(text='מחק מודעה', data='delete_adv_delete_adv')],
        [Button.inline(text='ערוך טקסט', data='delete_adv_edit_text')],
    ]
    ADV_INTERFACE_ACTIVE_BUTTON = [Button.inline(text='הפוך לפעילה', data='delete_adv_SET_POST_ACTIVE'),
                                   Button.inline(text='קבע מספר מודעה',data='delete_adv_set_post_position')]
    ADV_INTERFACE_INACTIVE_BUTTON = [Button.inline(text='הפוך ללא פעילה', data='delete_adv_SET_POST_INACTIVE'),
                                     Button.inline(text='קבע מספר מודעה',data='delete_adv_set_post_position')]
    ADV_INTERFACE_ROTATION_ACTIVE = [Button.inline(text='הפעל רוטציה למודעה', data='delete_adv_SET_POST_ROTATION_ACTIVE')]
    ADV_INTERFACE_ROTATION_INACTIVE = [Button.inline(text='כבה רוטציה למודעה', data='delete_adv_SET_POST_ROTATION_INACTIVE')]
    ADV_INTERFACE_BACK_BUTTON = [Button.inline(text='חזור', data='delete_adv_main_screen')]



    if update.data.decode() == 'delete_adv_main_screen':  # redirect to main menu
        await user_session.browse_disable()
        dash = await user_session.current_dashboard()
        await update.edit(text=dash[0],
                          buttons=dash[1],
                          file='bot_database/botphoto/manager/manager.jpg')

    elif not await user_session.browse_enable():  # constuct advertisement interfaces of advertisement for deletion
        adv = [AD["post_num"] for AD in ADS.find({}, {"_id": 0, "post_num": 1})]
        if len(adv) != 0:
            await user_session.browse_adv(adv)
            adv = ADS.find_one({'post_num': await user_session.current_browse_adv()})
            await sender_helper(adv)
        else:
            await update.answer(alert=True, message='אין מודעות פעילות')
            await user_session.browse_disable()

    elif await user_session.browse_check():  # go to next advertisement
        if 'edit_text' in update.data.decode() and MAINTENANCE == True:
            async with bot_manager.conversation(entity=update.chat, timeout=60) as aggregator:
                msg = await aggregator.send_message("שלח טקסט חדש")
                tmp_text = await aggregator.get_response()
                await bot_manager.delete_messages(entity=username,
                                                  message_ids=[msg,tmp_text])
                ADS.update_one({'post_num': await user_session.current_browse_adv()}, {'$set': {"text": tmp_text.text}})
                adv = ADS.find_one({'post_num': await user_session.current_browse_adv()})
                await sender_helper(adv)
                await update.answer()
                aggregator.cancel()

        elif 'SET_POST_ROTATION_ACTIVE' in update.data.decode() and MAINTENANCE == True:
            adv = ADS.find_one({'post_num': await user_session.current_browse_adv()})
            if adv['post_position'] == 1:
                await update.answer(alert=True, message='הפעולה לא ניתנת לביצווע על הפוסט הראשון בסדר')
                return

            ADS.update_one({'post_num': await user_session.current_browse_adv()}, {'$set': {'rotation': True}})
            adv = ADS.find_one({'post_num': await user_session.current_browse_adv()})
            await sender_helper(adv)
            await update.answer()

        elif 'SET_POST_ROTATION_INACTIVE' in update.data.decode() and MAINTENANCE == True:
            adv = ADS.find_one({'post_num': await user_session.current_browse_adv()})
            if adv['post_position'] == 1:
                await update.answer(alert=True, message='הפעולה לא ניתנת לביצווע על הפוסט הראשון בסדר')
                return

            ADS.update_one({'post_num': await user_session.current_browse_adv()}, {'$set': {'rotation': False}})
            adv = ADS.find_one({'post_num': await user_session.current_browse_adv()})
            await sender_helper(adv)
            await update.answer()

        elif 'set_post_position' in update.data.decode() and MAINTENANCE == True:
            async with bot_manager.conversation(entity=update.chat, timeout=60) as aggregator:
                tmp_lp = list(ADS.find({}, {"_id": 0, "post_position": 1}))
                lp = []
                for tmp_data in tmp_lp:
                    if tmp_data['post_position'] != 0:
                        lp.append(tmp_data['post_position'])
                del tmp_lp

                last_post = ADS.find({}, {"_id": 0, "post_num": 1}).sort("post_num", -1).limit(1).next()['post_num']
                msg = await aggregator.send_message(f'אנא קבע סדר מודעה עד '
                                                    f'\n'
                                                    f'{last_post}'
                                                    f'\n'
                                                    f'כולל')
                tmp_var_position = await aggregator.get_response()
                await bot_manager.delete_messages(entity=username,
                                                  message_ids=[msg,tmp_var_position])
                try:
                    tmp_var_position = int(tmp_var_position.text)
                    if tmp_var_position not in lp and tmp_var_position <= last_post:
                        ADS.update_one({'post_num': await user_session.current_browse_adv()},
                                       {'$set': {"post_position": tmp_var_position}})
                        await update.answer()
                        aggregator.cancel()
                    else:
                        msg = await aggregator.send_message('המיקום מוגדר למודעה אחרת',buttons=notification_button)
                        aggregator.cancel()
                        await update.answer()
                        return

                except Exception as ex:
                    await aggregator.send_message('הוכנס ערך לא תקין',buttons=notification_button)
                    aggregator.cancel()
                    await update.answer()
                    return

            adv = ADS.find_one({'post_num': await user_session.current_browse_adv()})
            await sender_helper(adv)

        elif update.data.decode() == 'delete_adv_SET_POST_ACTIVE' and MAINTENANCE == True:
            try:
                position_state = ADS.find_one({'post_num': await user_session.current_browse_adv()},{"_id": 0, "post_position": 1})
                if position_state['post_position'] == 0:
                    await update.answer(alert=True,message='יש להגדיר קודם סדר מודעה')
                    return
            except Exception as ex:
                await update.answer(alert=True, message='גרסאת המודעה לא תואמת את גרסאת הבוט')
                return
            ADS.update_one({'post_num': await user_session.current_browse_adv()},
                            {'$set':{"status":'פעילה'}})
            adv = ADS.find_one({'post_num': await user_session.current_browse_adv()})
            await sender_helper(adv)

        elif update.data.decode() == 'delete_adv_SET_POST_INACTIVE' and MAINTENANCE == True:
            ADS.update_one({'post_num': await user_session.current_browse_adv()},
                           {'$set':{"status":'לא פעילה'}})
            adv = ADS.find_one({'post_num': await user_session.current_browse_adv()})
            await sender_helper(adv)

        elif update.data.decode() == 'delete_adv_SET_POST_ACTIVE' or update.data.decode() == 'delete_adv_SET_POST_INACTIVE' or 'set_post_position' in update.data.decode():
            await update.answer(alert=True,message='ניתן לבצע את הפעולה רק כשא הבוט כבוי')
            return

        if update.data.decode() == 'delete_adv_next_adv':
            if not await user_session.browse_singelton():
                adv = ADS.find_one({'post_num': await user_session.next_browse_adv()})
                await sender_helper(adv)
            else:
                await update.answer(alert=True, message='אין מודעות נוספות')

        elif update.data.decode() == 'delete_adv_previous_adv':  # goes back to previous advertisement
            if not await user_session.browse_singelton():
                adv = ADS.find_one({'post_num': await user_session.previous_browse_adv()})
                await sender_helper(adv)
            else:
                await update.answer(alert=True, message='אין מודעות נוספות')

        elif update.data.decode() == 'delete_adv_delete_adv' and MAINTENANCE == True:  # delete advertisement
            ADS.delete_one({'post_num': await user_session.current_browse_adv()})
            if await user_session.remove_browse_adv():
                adv = ADS.find_one({'post_num': await user_session.current_browse_adv()})
                await sender_helper(adv)
            else:
                await user_session.browse_disable()
                c_dash = await user_session.current_dashboard()
                await update.edit(text=c_dash[0],
                                  file='bot_database/botphoto/manager/manager.jpg',
                                  buttons=c_dash[1])
        elif update.data.decode() == 'delete_adv_delete_adv' and MAINTENANCE == False:
            await update.answer(alert=True,message='כדי למחוק מודעות הבוט צריך להיות כבוי')

    raise events.StopPropagation
@bot_manager.on(events.CallbackQuery(pattern='add_channel_dash'))
async def add_channel(update):
    user_info = await update.get_sender()
    username = user_info.username
    async with bot_manager.conversation(entity=update.chat, timeout=60) as aggregator:
        try:
            msg = await aggregator.send_message('אנא שלח קישורים לערוצים או הקבוצות בצורה הבאה: '
                                                '\nexamplethatnotexist\nexamplethatnotexist2\n'
                                                'או שלח "בטל" לביטול ההודעה')
            invite_links = await aggregator.get_response()
            await bot_manager.delete_messages(entity=username,
                                              message_ids=[msg, invite_links])
            if invite_links.message == 'בטל':  # quick patch
                await update.answer(alert=True, message='בוטל')
                aggregator.cancel()
            invite_links = [{'Link': dummy} for dummy in invite_links.message.split('\n')]
            GROUPS.insert_many(invite_links)
            await update.answer(alert=True, message=f'כמות הקבוצות שצורפו היא: {str(len(invite_links))}')
        except:
            await update.answer(alert=True, message='אין מודעות פעילות')
        # TODO:
        # ensure validation mechanism is good
        # create unique record in db for link field

    raise events.StopPropagation
@bot_manager.on(events.CallbackQuery(pattern='del_channel'))
async def del_channel_dashboard(update):
    user_info = await update.get_sender()
    username = user_info.username
    user_session = await user_sessions.get_session(username)

    async with bot_manager.conversation(entity=update.chat, timeout=60) as aggregator:
        groups = [[Button.inline(text=group['Link'], data=group['Link'] + '_del_channel')] for group in GROUPS.find()]
        groups.append([Button.inline(text='חזור', data='step_back')])
        await user_session.add_msg(buttons=groups)
        while True:
            try:
                msg = await update.edit(buttons=groups)
                break
            except Exception as ex:
                pass
    raise events.StopPropagation
@bot_manager.on(events.CallbackQuery(pattern=r'.*_del_channel.*'))
async def del_channel(update):
    user_info = await update.get_sender()
    username = user_info.username
    user_session = await user_sessions.get_session(username)
    GROUPS.delete_one({'Link': update.data[:len(update.data) - 12].decode()})
    groups = [[Button.inline(text=group['Link'], data=group['Link'] + '_del_channel')] for group in GROUPS.find()]
    groups.append([Button.inline(text='חזור', data='step_back')])
    await user_session.previous_dashboard()
    await user_session.add_msg(buttons=groups)
    while True:
        try:
            msg = await update.edit(buttons=groups)
            break
        except Exception as ex:
            pass

    raise events.StopPropagation
@bot_manager.on(events.CallbackQuery(pattern=r'add_user_distributors_dash'))
async def add_user_distributors_dashboard(update):
    user_info = await update.get_sender()
    username = user_info.username
    user_session = await user_sessions.get_session(username)
    user_info = await update.get_sender()
    username = user_info.username
    user_session = await user_sessions.get_session(username)
    try:
        async with bot_manager.conversation(entity=update.chat, timeout=60) as aggregator:
            await update.answer(alert=True, message='לביטול הפעולה אנא הגב בטל')

            request = await aggregator.send_message('אנא שלח את המספר שאותו ברצונך לצרף: כולל קידומת.')
            phone_number = await aggregator.get_response()
            await bot_manager.delete_messages(entity=username,
                                              message_ids=[request, phone_number])
            if phone_number.text == 'בטל':
                await aggregator.send_message(message='הפעולה בוטלה',
                                              buttons=notification_button)
                aggregator.cancel()
                return
            distributor = Distributor(phone_number=phone_number.text,
                                      api_id=api_info[0]['api_id'],
                                      api_hash=api_info[0]['api_hash'],
                                      bot_manager=bot_manager)
            if await distributor.code_listener_send(aggregator):
                request = await aggregator.send_message('הזן את הקוד שקיבלת')
                code = await aggregator.get_response()

                if code.text == 'בטל':
                    await aggregator.send_message(message='הפעולה בוטלה',
                                                  buttons=notification_button)
                    aggregator.cancel()
                    return

                await bot_manager.delete_messages(entity=username,
                                                  message_ids=[request, code])
                try:
                    result_tmp = await distributor.insert_distributor(code.text)
                    if not result_tmp:
                        await aggregator.send_message(message='התוכנה מקבלת רק מספרים שיש להם שם משתמש,'
                                                              ' אנא צור שם משתמש והכנס שנית.',
                                                      buttons=notification_button)
                    elif result_tmp == 'SessionPasswordNeededError':
                        request = await aggregator.send_message('הזן סיסמאת אימות דו-שלבי')
                        code = await aggregator.get_response()

                        if code.text == 'בטל':
                            await aggregator.send_message(message='הפעולה בוטלה',
                                                          buttons=notification_button)
                            aggregator.cancel()
                            return

                        await bot_manager.delete_messages(entity=username,
                                                          message_ids=[request, code])
                        if not await distributor.insert_distributor_2fa(code.text):
                            await aggregator.send_message(message='התוכנה מקבלת רק מספרים שיש להם שם משתמש,'
                                                                  ' אנא צור שם משתמש והכנס שנית.',
                                                          buttons=notification_button)
                    else:
                        aggregator.cancel()
                except Exception as ex:
                    print(f"[{datetime.now()}] - unhandled error under 2fa add user function")

    except Exception as ex:
        custom_logger.flush()
        custom_logger.write(f'[{datetime.now()}] - the following error occur '
                            f'while add_user_listener_dashboard: {str(ex)}\n')
        custom_logger.flush()
@bot_manager.on(events.CallbackQuery(pattern='del_user_distributors_dash$'))
async def del_user_distributors_dashboard(update):
    user_info = await update.get_sender()
    username = user_info.username
    user_session = await user_sessions.get_session(username)

    USERS_INTERFACE = [[Button.inline(text=f"{user['username']}:{user['phone']}",
                                      data=f"del_user_distributors_dash_{user['phone']}")]
                       for user in BOTS.find({"phone": {"$exists": True}, "role": "distributors"})]
    USERS_INTERFACE.append([Button.inline(text='חזור', data='step_back')])
    await user_session.add_msg(buttons=USERS_INTERFACE)
    while True:
        try:
            msg = await update.edit(buttons=USERS_INTERFACE)
            break
        except Exception as ex:
            pass

    raise events.StopPropagation
@bot_manager.on(events.CallbackQuery(pattern=r'.*del_user_distributors_dash.*'))
async def del_distributors_user(update):
    global MAINTENANCE
    global DISTRIBUTORS
    if MAINTENANCE == False:
        while True:
            try:
                await update.answer(alert=True, message='ניתן לביצוע רק כאשר הבוט מכובה')
                return
            except Exception as ex:
                pass

    user_info = await update.get_sender()
    username = user_info.username
    user_session = await user_sessions.get_session(username)

    tmp = Distributor(api_id=api_info[0]['api_id'],
                      api_hash=api_info[0]['api_hash'],
                      phone_number=update.data.decode().split('dash_')[1],
                      bot_manager=bot_manager)
    await tmp.delete_client()
    USERS_INTERFACE = [[Button.inline(text=f"{user['username']}:{user['phone']}",
                                      data=f"del_user_distributors_dash_{user['phone']}")]
                       for user in BOTS.find({"phone": {"$exists": True}, "role": "distributors"})]
    USERS_INTERFACE.append([Button.inline(text='חזור', data='step_back')])
    await user_session.previous_dashboard()
    await user_session.add_msg(buttons=USERS_INTERFACE)
    while True:
        try:
            msg = await update.edit(buttons=USERS_INTERFACE)
            break
        except Exception as ex:
            pass

    raise events.StopPropagation
@bot_manager.on(events.CallbackQuery(pattern=r'MAINTENANCE_(ENABLE|DISABLE)'))
async def maintenance_state(update):
    global MAINTENANCE
    global GROUPS_DATA
    global GROUPS_ENTITY
    global ADS_DATA
    global LISTENER
    global DISTRIBUTORS

    # protectors
    if not MAINTENANCE and update.data.decode() == 'MAINTENANCE_DISABLE':
        await update.answer(alert=True, message='הבוט כבר במצב עבודה')
        return
    elif MAINTENANCE and update.data.decode() == 'MAINTENANCE_ENABLE':
        await update.answer(alert=True, message='הבוט כבר במצב כבוי')
        return

    if update.data.decode() == 'MAINTENANCE_ENABLE':
        MAINTENANCE = True
        await DISTRIBUTORS.deactive_distributors()
        await update.answer(alert=True, message='בוט פרסום נכבה')


    elif update.data.decode() == 'MAINTENANCE_DISABLE':
        try:
            GROUPS_DATA = [GROUP for GROUP in GROUPS.find({}, {'_id': 0, 'Link': 1})]
            GROUPS_ENTITY = [await bot_manager.get_entity('@' + chat['Link']) for chat in GROUPS_DATA]
        except Exception as ex:
            custom_logger.flush()
            custom_logger.write(f'[{datetime.now()}] - the following error occur while loading entities of groups '
                                f'inside maintenance_state: {str(ex)}\n')

        TMP_ADS_DATA = []
        ADS_DATA = []

        for AD in ADS.find():
            if AD['status'] == 'פעילה':
                TMP_ADS_DATA.append(AD)

        if len(TMP_ADS_DATA) != 0:
            TMP_ADS_DATA.sort(key=lambda x: x['post_position'])
            for data in TMP_ADS_DATA:
                if data['post_position'] != 0:
                    ADS_DATA.append(data)
            del TMP_ADS_DATA


        if len(GROUPS_DATA) == 0 and len(ADS_DATA) == 0:
            await update.answer(alert=True, message='אין לא מודעה ולא קבוצת יעד לפרסום, הבוט לא יופעל')
            return
        elif len(GROUPS_DATA) == 0:
            await update.answer(alert=True, message='אין אף קבוצת פרסום ולכן הבוט לא יופעל')
            return
        elif len(ADS_DATA) == 0:
            await update.answer(alert=True, message='אין אף מודעה פעילה ולכן הבוט לא יופעל')
            return
        elif not await load_clients():
            await update.answer(alert=True, message='צריך לחשוב איך להציע למשתמש')
            return
        MAINTENANCE = False
        await update.answer(alert=True, message='בוט פרסום הודלק')
    raise events.StopPropagation

async def load_clients():
    global ADS_DATA
    global DISTRIBUTORS
    global GROUPS
    try:
        DISTRIBUTORS = DistributorManager(api_id=api_info[0]['api_id'],
                                          api_hash=api_info[0]['api_hash'],
                                          bot_manager=bot_manager)
        await DISTRIBUTORS.active_distributors(ADS_DATA)
        return True
    except Exception as ex:
        custom_logger.flush()
        custom_logger.write(f'[{datetime.now()}] - the following error occur while load_clients: {str(ex)}\n')
        custom_logger.flush()
        return False

async def rate_limit_checker(): #deprected code
    global PPM,PPM_TIMER,PPM_RELOAD
    if time.time() - PPM_TIMER > 60:
            PPM_TIMER = time.time()
            PPM = PPM_RELOAD
            return True
    elif time.time() - PPM_TIMER < 60:
        if PPM >= 0:
            return True
        return False

async def main():
    global LISTENER
    global DISTRIBUTORS
    global NOTIFICATION_USER
    global PPM,PPM_TIMER,PPM_RELOAD
    global GROUPS_DATA
    global GROUPS_ENTITY
    global POST_AGGREGATOR_STATE
    global POST_INTERVAL
    PPM_TIMER = time.time()

    DISTRIBUTORS = DistributorManager(api_id=api_info[0]['api_id'],
                                      api_hash=api_info[0]['api_hash'],
                                      bot_manager=bot_manager)


    while True:
        CYCLE = 0
        if MAINTENANCE == False and len(GROUPS_ENTITY) != 0:
            for chat in GROUPS_ENTITY:
                tmp_aggregator = asyncio.gather(DISTRIBUTORS.send_post(chat,CYCLE))
                CYCLE = 1
            POST_AGGREGATOR_STATE = True
            await tmp_aggregator
            POST_AGGREGATOR_STATE = False
            await asyncio.sleep(POST_INTERVAL)


        else:
            await asyncio.sleep(1)

if __name__ == '__main__':
    asyncio.get_event_loop().run_until_complete(main())
