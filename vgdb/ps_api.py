from concurrent.futures import as_completed
from datetime import datetime
import json
import time
from urllib.parse import urlparse
from urllib.parse import parse_qs

import numpy as np
import requests
from requests_futures.sessions import FuturesSession
from tqdm import tqdm


class PlaystationClient():

    def __init__(self, npsso):
        self.npsso = npsso
        self.access_token = None

        self.session = FuturesSession()
        self.WAIT_TIME = 0.5

        if not self.access_token:
            self.access_token = self._get_access_token(self.npsso)

    def _get_access_token(self, npsso):
        """Return auth code from PS NPSSO"""

        # Get auth code
        cookies = {'npsso': npsso}
        request_url = "https://ca.account.sony.com/api/authz/v3/oauth/authorize?access_type=offline&client_id=09515159-7237-4370-9b40-3806e67c0891&response_type=code&scope=psn:mobile.v2.core%20psn:clientapp&redirect_uri=com.scee.psxandroid.scecompcall://redirect"

        r = requests.get(
            request_url,
            cookies=cookies,
            allow_redirects=False
        )
        time.sleep(self.WAIT_TIME)
        auth_code = parse_qs(urlparse(r.headers['location']).query)['code']

        # Get access token
        data = {
            'code': auth_code,
            'redirect_uri': "com.scee.psxandroid.scecompcall://redirect",
            'grant_type': "authorization_code",
            'token_format': "jwt"
        }
        headers = {
            "Authorization": "Basic MDk1MTUxNTktNzIzNy00MzcwLTliNDAtMzgwNmU2N2MwODkxOnVjUGprYTV0bnRCMktxc1A="
        }
        r = requests.post(
            "https://ca.account.sony.com/api/authz/v3/oauth/token",
            data=data,
            headers=headers
        )
        time.sleep(self.WAIT_TIME)
        access_token = json.loads(r.text)['access_token']

        return access_token

    def get_played_titles(self):
        # == Get titles
        start = time.time()
        print('Playstation Played Titles...')
        headers = {"Authorization": f"Bearer {self.access_token}"}
        r = requests.get(
            "https://m.np.playstation.com/api/gamelist/v2/users/me/titles?categories=ps4_game,ps5_native_game&limit=250&offset=0",
            headers=headers
        )
        time.sleep(self.WAIT_TIME)
        titles = json.loads(r.text)['titles']
        titles = [
            {
                'ps_np_title_id': title['titleId'],
                'ps_np_comm_id': None,
                'title': title['name'],
                'console': title['category'],
                'playtime': title['playDuration'],
                'trophy_weighted_progress': None,
                'completed_trophies': None,
                'total_trophies': None,
                'first_played': title['firstPlayedDateTime'],
                'last_played': title['lastPlayedDateTime'],
                'genres': title['concept']['genres']
                
            } for title in titles
        ]

        # Convert datetime str to unix epoch
        for title in titles:
            title['first_played'] = datetime.strptime(title['first_played'], "%Y-%m-%dT%H:%M:%S.%fZ").timestamp()
            title['last_played'] = datetime.strptime(title['last_played'], "%Y-%m-%dT%H:%M:%S.%fZ").timestamp()

        # Process playtime into a float
        for title in titles:
            playtime_str = title['playtime']
            playtime_str = playtime_str[2:]  # Remove 'PT' at start

            # Example: 'PT14H5M57S'
            playtime_hours = 0
            if 'H' in playtime_str:
                hours, playtime_str = playtime_str.split('H')
                playtime_hours += float(hours)
            if 'M' in playtime_str:
                minutes, playtime_str = playtime_str.split('M')
                playtime_hours += (float(minutes)/60)
            if 'S' in playtime_str:
                seconds, playtime_str = playtime_str.split('S')
                playtime_hours += (float(seconds)/360)

            playtime_minutes = playtime_hours*60

            title['playtime'] = np.round(playtime_minutes, 1)
        print(f'Playstation Played Titles [{time.time()-start:.2f} seconds]')

        #== Get trophy information
        start = time.time()
        print('Playstation Played Titles Trophies...')
        titles = self._enrich_with_trophies(titles)
        print(f'Playstation Played Titles Trophies [{time.time()-start:.2f} seconds]')

        return titles

    def _enrich_with_trophies(self, titles):
        """
        Async enriches records list with trophies data
        """
        futures=[]
        for title in titles:
            headers = {"Authorization": f"Bearer {self.access_token}"}
            # NOTE: In the future, you can supply multiple np_title_ids => npTitleIds={",".join(query_np_title_ids)} of maybe max 5 ids
            future = self.session.get(
                f'https://m.np.playstation.com/api/trophy/v1/users/me/titles/trophyTitles?npTitleIds={title["ps_np_title_id"]}',
                headers=headers
            )
            future.title = title
            futures.append(future)

        titles_with_trophy_data = []
        for future in as_completed(futures):
            try:
                # Get response
                resp = future.result()
                title = future.title
                trophy_json = resp.json()['titles'][0]

                # Handle status
                if resp.status_code > 299:
                    raise Exception

                # Process trophies
                if len(trophy_json['trophyTitles']) > 0:
                    title['ps_np_comm_id'] = trophy_json['trophyTitles'][0]['npCommunicationId']
                    title['trophy_weighted_progress'] = trophy_json['trophyTitles'][0]['progress']  # Can there be multiple trophy sets per npTitleId?
                    title['completed_trophies'] = \
                        int(trophy_json['trophyTitles'][0]["earnedTrophies"]['bronze']) +\
                        int(trophy_json['trophyTitles'][0]["earnedTrophies"]['silver']) +\
                        int(trophy_json['trophyTitles'][0]["earnedTrophies"]['gold']) +\
                        int(trophy_json['trophyTitles'][0]["earnedTrophies"]['platinum'])
                    title['total_trophies'] = \
                        int(trophy_json['trophyTitles'][0]["definedTrophies"]['bronze']) +\
                        int(trophy_json['trophyTitles'][0]["definedTrophies"]['silver']) +\
                        int(trophy_json['trophyTitles'][0]["definedTrophies"]['gold']) +\
                        int(trophy_json['trophyTitles'][0]["definedTrophies"]['platinum'])
                else:
                    print(f'No trophies for [{title["ps_np_title_id"]}] {title["title"]}')

                titles_with_trophy_data.append(title)

            except:
                print(f'[{resp.status_code}] on {future.title["title"]}')
                import sys; sys.exit(1)

        return titles_with_trophy_data
            
