import os
import shutil
folders = os.listdir()
sessions = r'{}\sessions'.format(os.getcwd())
dst_location = f'{os.getcwd()}/sessions_destination_loc'
for folder in folders:
    if 'session_extractor.py' in folder or 'sessions_destination_loc' in folder:
        continue
    session_file = os.listdir(r'{}{}'.format(os.getcwd(),folder))[0]
    path = f'{os.getcwd()}/{folder}/{session_file}'
    shutil.copy(path,dst_location)