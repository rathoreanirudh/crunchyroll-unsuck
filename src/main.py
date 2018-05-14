import sys
import subprocess
import curses
import os
from gui import InputHandler, ItemWidget, BrowserWidget, ContainerWidget, LogWidget, InactiveItemWidget
from gui import ShortcutWidget
from gui import BaseLayout, HorizontalLayout, VerticalLayout, Value, App
from api.crunchyroll import CrunchyrollAPI
import constants

api = CrunchyrollAPI()

class Episode(object):
    def get_id(self):
        """ ID used in cache files
        """
        pass

    def open(self):
        """ opens the episode and sets the playhead
        """
        pass

    def get_episode_number(self):
        pass

    def get_name(self):
        pass

    def get_collection(self):
        pass


class CREpisode(Episode):
    def __init__(self, data):
        self.data = data

    def get_id(self):
        return 'CR-' + self.data['media_id']

    def open(self, log=(lambda x:None)):
        mpv_args = ("--start=%d " % max(0, constants.get_playhead(self.get_id()) - 5)) + \
            "--term-status-msg \"Playback Status: ${{=time-pos}} ${{=duration}} \" {filename}"
        args = [
            'streamlink', self.data['url'], 'best', '--verbose-player', "-a",
            mpv_args
        ]
        p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        log('$ ' + ' '.join(args))
        playhead = None
        for line in p.stdout:
            line = line.decode().strip()
            if line[:16] == 'Playback Status:':
                playhead, total_time = [float(x) for x in line.split()[-2:]]
            log(line)

        if playhead:
            constants.update_history(self.get_id(), playhead, total_time)
        p.wait()

    def get_episode_number(self):
        return self.data['episode_number']

    def get_name(self):
        return self.data['name']

    def get_collection(self):
        return self.data.get('collection_id', None)


class Anime(object):
    def get_id(self):
        pass

    def get_episodes(self):
        """ returns [(collection, [episodes...])...]
        """
        pass

    def get_name(self):
        pass


class CRAnime(object):
    def __init__(self, data):
        self.data = data

    def get_id(self):
        return 'CR-' + self.data['series_id']

    def get_episodes(self, log=(lambda x:None)):
        log('Fetching episodes...')
        episodes = [CREpisode(episode) for episode in api.list_media(series_id=self.data['series_id'], sort='desc', limit=1000)]
        log('Fetched %d episodes' % len(episodes))
        return episodes

    def get_collections(self, log=(lambda x:None)):
        log('Fetching collections...')
        collections = api.list_collections(series_id=self.data['series_id'], limit=50)
        collections = {c['collection_id']: c['name'] for c in collections}
        log('Fetched %d collections' % len(collections))
        return collections

    def get_name(self):
        return self.data['name']


def generate_control_switch(lst, active=0):
    cur_control_idx = active
    lst_dict = {}
    for idx, (key, obj) in enumerate(lst):
        obj.unfocus()
        lst_dict[key] = idx
        lst[idx] = obj
    lst[active].focus()

    def _next_switch():
        nonlocal cur_control_idx
        lst[cur_control_idx].unfocus()
        lst[cur_control_idx].redraw()
        cur_control_idx = (cur_control_idx + 1) % len(lst)
        lst[cur_control_idx].focus()
        lst[cur_control_idx].redraw()
        lst[cur_control_idx].get_app().set_control(lst[cur_control_idx])

    def _prev_switch():
        nonlocal cur_control_idx
        lst[cur_control_idx].unfocus()
        lst[cur_control_idx].redraw()
        cur_control_idx = (cur_control_idx - 1 + len(lst)) % len(lst)
        lst[cur_control_idx].focus()
        lst[cur_control_idx].redraw()
        lst[cur_control_idx].get_app().set_control(lst[cur_control_idx])

    def _switch_to(key):
        nonlocal cur_control_idx
        lst[cur_control_idx].unfocus()
        lst[cur_control_idx].redraw()
        cur_control_idx = lst_dict[key]
        lst[cur_control_idx].focus()
        lst[cur_control_idx].redraw()
        lst[cur_control_idx].get_app().set_control(lst[cur_control_idx])

    return _prev_switch, _next_switch, _switch_to


class MyApp(App):
    def __init__(self, stdscr):
        root = BaseLayout(Value(curses.COLS), Value(curses.LINES), None)
        super().__init__(stdscr, root)

        main_container = ContainerWidget(root, False, constants.APP_NAME + ' v' + constants.APP_VERSION, center=True, style=(curses.A_BOLD|curses.A_UNDERLINE))
        l4 = VerticalLayout(Value(1, Value.VAL_RELATIVE), Value(1, Value.VAL_RELATIVE), main_container)

        l1 = HorizontalLayout(Value(1, Value.VAL_RELATIVE), Value(0.8, Value.VAL_RELATIVE), l4)
        l2 = BaseLayout(Value(0.3, Value.VAL_RELATIVE), Value(1, Value.VAL_RELATIVE), l1)

        l5 = BaseLayout(Value(1, Value.VAL_RELATIVE), Value(-1, Value.VAL_ABSOLUTE), l4)
        c3 = ContainerWidget(l5, True, "Log")

        c1 = ContainerWidget(l2, True, "Anime")
        c2 = ContainerWidget(l1, True, "Episodes")
        lst1 = BrowserWidget(c1)
        lst2 = BrowserWidget(c2)


        self.anime_view_shortcuts = [
            ('s', 'sort', sys.exit),
            ('d', 'delete', sys.exit),
            ('q', 'exit', sys.exit),
        ]
        s1 = ShortcutWidget(l4, c1, self.anime_view_shortcuts)

        anime_queue = [CRAnime(anime['series']) for anime in api.get_queue('anime')]
        for anime in anime_queue:
            ItemWidget(lst1, anime.get_name(), anime)

        # Register events
        root.register_event('q', sys.exit)
        self.prev_switch, self.next_switch, self.switch_to = generate_control_switch([('anime', lst1), ('episodes', lst2)], active=0)
        l1.register_event('l', self.next_switch)
        l1.register_event('KEY_RIGHT', self.next_switch)
        l1.register_event('h', self.prev_switch)
        l1.register_event('KEY_LEFT', self.prev_switch)
        lst1.set_selection_callback(self.list_episodes)
        lst2.set_selection_callback(self.open_episode)
        self.anime_list_widget = lst1
        self.episode_list_widget = lst2
        self.directory_container = c1
        self.episode_container = c2

        self.set_log_widget(LogWidget(c3))
        self.set_control(lst1)

    def tablize(self, rows, extra_padding):
        ret = []
        offset = []
        prev_offset = 0
        for col in range(len(rows[0])):
            offset.append(0)
            for row in rows:
                ret.append('')
                offset[-1] = max(offset[-1], prev_offset + len(row[col]) + extra_padding)
            prev_offset = offset[-1]

        for col in range(len(rows[0])):
            for idx, row in enumerate(rows):
                ret[idx] += row[col]
                if col != len(rows[0])-1:
                    ret[idx] += ' ' * (offset[col] - len(ret[idx]))

        return ret

    def list_episodes(self, selected_item):
        anime = selected_item.get_data()
        episodes = anime.get_episodes(self.log)
        collections = anime.get_collections(self.log)

        episode_item_text = []
        latest_accessed_episode = None
        latest_accessed_episode_time = 0
        for episode in episodes:
            last_access_time = constants.get_last_accessed(episode.get_id())
            if last_access_time > latest_accessed_episode_time:
                latest_accessed_episode, latest_accessed_episode_time = episode, last_access_time
            episode_item_text.append((episode.get_episode_number(), episode.get_name()))
        episode_item_text = self.tablize(episode_item_text, 5)

        self.episode_list_widget.clear_children()
        current_collection = None
        for episode_text, episode in zip(episode_item_text, episodes):
            if episode.get_collection() != current_collection:
                current_collection = episode.get_collection()
                if current_collection in collections:
                    InactiveItemWidget(self.episode_list_widget, collections[current_collection])
            ItemWidget(self.episode_list_widget, episode_text, episode, default=(episode == latest_accessed_episode))

        self.switch_to('episodes')

    def open_episode(self, selected_item):
        episode = selected_item.get_data()
        episode.open(self.log)


def main(stdscr):
    stdscr = curses.initscr()
    curses.start_color()
    curses.curs_set(0)
    stdscr.keypad(True)
    curses.use_default_colors()

    app = MyApp(stdscr)
    app.run()

curses.wrapper(main)
