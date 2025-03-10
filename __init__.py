# Copyright 2017, Mycroft AI Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import datetime
import re
import pytz
import time
import tzlocal
from astral import Astral
import holidays

from adapt.intent import IntentBuilder
import mycroft.audio
from mycroft.messagebus.message import Message
from mycroft import MycroftSkill, intent_handler, intent_file_handler
from mycroft.util.time import now_utc, default_timezone, to_local
from mycroft.skills.core import resting_screen_handler
from lingua_franca.parse import extract_datetime, fuzzy_match, extract_number, normalize
from lingua_franca.format import pronounce_number, nice_date, nice_time
from lingua_franca.lang.format_de import nice_time_de, pronounce_ordinal_de


class TimeSkill(MycroftSkill):

    def __init__(self):
        super(TimeSkill, self).__init__("TimeSkill")
        self.astral = Astral()
        self.displayed_time = None
        self.display_tz = None
        self.answering_query = False

    def initialize(self):
        # Start a callback that repeats every 10 seconds
        # TODO: Add mechanism to only start timer when UI setting
        #       is checked, but this requires a notifier for settings
        #       updates from the web.
        now = datetime.datetime.now()
        callback_time = (datetime.datetime(now.year, now.month, now.day,
                                           now.hour, now.minute) +
                         datetime.timedelta(seconds=60))
        self.schedule_repeating_event(self.update_display, callback_time, 10)

    # TODO:19.08 Moved to MycroftSkill
    @property
    def platform(self):
        """ Get the platform identifier string

        Returns:
            str: Platform identifier, such as "mycroft_mark_1",
                 "mycroft_picroft", "mycroft_mark_2".  None for nonstandard.
        """
        if self.config_core and self.config_core.get("enclosure"):
            return self.config_core["enclosure"].get("platform")
        else:
            return None

    @resting_screen_handler('Time and Date')
    def handle_idle(self, message):
        self.gui.clear()
        self.log.info('Activating Time/Date resting page')
        self.gui['time_string'] = self.get_display_current_time()
        self.gui['ampm_string'] = ''
        self.gui['date_string'] = self.get_display_date()
        self.gui['weekday_string'] = self.get_weekday()
        self.gui['month_string'] = self.get_month_date()
        self.gui['year_string'] = self.get_year()
        self.gui.show_page('idle.qml')

    @property
    def use_24hour(self):
        return self.config_core.get('time_format') == 'full'

    def get_timezone(self, locale):
        try:
            # This handles common city names, like "Dallas" or "Paris"
            return pytz.timezone(self.astral[locale].timezone)
        except:
            pass

        try:
            # This handles codes like "America/Los_Angeles"
            return pytz.timezone(locale)
        except:
            pass

        # Check lookup table for other timezones.  This can also
        # be a translation layer.
        # E.g. "china = GMT+8"
        timezones = self.translate_namedvalues("timezone.value")
        for timezone in timezones:
            if locale.lower() == timezone.lower():
                # assumes translation is correct
                return pytz.timezone(timezones[timezone].strip())

        # Now we gotta get a little fuzzy
        # Look at the pytz list of all timezones. It consists of
        # Location/Name pairs.  For example:
        # ["Africa/Abidjan", "Africa/Accra", ... "America/Denver", ...
        #  "America/New_York", ..., "America/North_Dakota/Center", ...
        #  "Cuba", ..., "EST", ..., "Egypt", ..., "Etc/GMT+3", ...
        #  "Etc/Zulu", ... "US/Eastern", ... "UTC", ..., "Zulu"]
        target = locale.lower()
        best = None
        for name in pytz.all_timezones:
            normalized = name.lower().replace("_", " ").split("/") # E.g. "Australia/Sydney"
            if len(normalized) == 1:
                pct = fuzzy_match(normalized[0], target)
            elif len(normalized) >= 2:
                pct = fuzzy_match(normalized[1], target)                           # e.g. "Sydney"
                pct2 = fuzzy_match(normalized[-2] + " " + normalized[-1], target)  # e.g. "Sydney Australia" or "Center North Dakota"
                pct3 = fuzzy_match(normalized[-1] + " " + normalized[-2], target)  # e.g. "Australia Sydney"
                pct = max(pct, pct2, pct3)
            if not best or pct >= best[0]:
                best = (pct, name)
        if best and best[0] > 0.8:
           # solid choice
           return pytz.timezone(best[1])
        if best and best[0] > 0.3:
            # Convert to a better speakable version
            say = re.sub(r"([a-z])([A-Z])", r"\g<1> \g<2>", best[1])  # e.g. EasterIsland  to "Easter Island"
            say = say.replace("_", " ")  # e.g. "North_Dakota" to "North Dakota"
            say = say.split("/")  # e.g. "America/North Dakota/Center" to ["America", "North Dakota", "Center"]
            say.reverse()
            say = " ".join(say)   # e.g.  "Center North Dakota America", or "Easter Island Chile"
            if self.ask_yesno("did.you.mean.timezone", data={"zone_name": say}) == "yes":
                return pytz.timezone(best[1])

        return None

    def get_local_datetime(self, location, dtUTC=None):
        if not dtUTC:
            dtUTC = now_utc()
        if self.display_tz:
            # User requested times be shown in some timezone
            tz = self.display_tz
        else:
            tz = self.get_timezone(self.location_timezone)

        if location:
            tz = self.get_timezone(location)
        if not tz:
            self.speak_dialog("time.tz.not.found", {"location": location})
            return None

        return dtUTC.astimezone(tz)

    def get_display_date(self, day=None, location=None):
        if not day:
            day = self.get_local_datetime(location)
        if self.config_core.get('date_format') == 'MDY':
            return day.strftime("%-m/%-d/%Y")
        else:
            return day.strftime("%Y/%-d/%-m")

    def get_display_current_time(self, location=None, dtUTC=None):
        # Get a formatted digital clock time based on the user preferences
        dt = self.get_local_datetime(location, dtUTC)
        if not dt:
            return None

        return nice_time(dt, self.lang, speech=False,
                         use_24hour=self.use_24hour)

    def get_spoken_current_time(self, location=None, dtUTC=None, force_ampm=False):
        # Get a formatted spoken time based on the user preferences
        dt = self.get_local_datetime(location, dtUTC)
        if not dt:
            return

        # speak AM/PM when talking about somewhere else
        say_am_pm = bool(location) or force_ampm

        s = nice_time(dt, self.lang, speech=True,
                      use_24hour=self.use_24hour, use_ampm=say_am_pm)
        # HACK: Mimic 2 has a bug with saying "AM".  Work around it for now.
        if say_am_pm:
            s = s.replace("AM", "A.M.")
        return s

    def display(self, display_time):
        if display_time:
            if self.platform == "mycroft_mark_1":
                self.display_mark1(display_time)
            self.display_gui(display_time)

    def display_mark1(self, display_time):
        # Map characters to the display encoding for a Mark 1
        # (4x8 except colon, which is 2x8)
        code_dict = {
            ':': 'CIICAA',
            '0': 'EIMHEEMHAA',
            '1': 'EIIEMHAEAA',
            '2': 'EIEHEFMFAA',
            '3': 'EIEFEFMHAA',
            '4': 'EIMBABMHAA',
            '5': 'EIMFEFEHAA',
            '6': 'EIMHEFEHAA',
            '7': 'EIEAEAMHAA',
            '8': 'EIMHEFMHAA',
            '9': 'EIMBEBMHAA',
        }

        # clear screen (draw two blank sections, numbers cover rest)
        if len(display_time) == 4:
            # for 4-character times, 9x8 blank
            self.enclosure.mouth_display(img_code="JIAAAAAAAAAAAAAAAAAA",
                                         refresh=False)
            self.enclosure.mouth_display(img_code="JIAAAAAAAAAAAAAAAAAA",
                                         x=22, refresh=False)
        else:
            # for 5-character times, 7x8 blank
            self.enclosure.mouth_display(img_code="HIAAAAAAAAAAAAAA",
                                         refresh=False)
            self.enclosure.mouth_display(img_code="HIAAAAAAAAAAAAAA",
                                         x=24, refresh=False)

        # draw the time, centered on display
        xoffset = (32 - (4*(len(display_time))-2)) / 2
        for c in display_time:
            if c in code_dict:
                self.enclosure.mouth_display(img_code=code_dict[c],
                                             x=xoffset, refresh=False)
                if c == ":":
                    xoffset += 2  # colon is 1 pixels + a space
                else:
                    xoffset += 4  # digits are 3 pixels + a space

        if self._is_alarm_set():
            # Show a dot in the upper-left
            self.enclosure.mouth_display(img_code="CIAACA", x=29, refresh=False)
        else:
            self.enclosure.mouth_display(img_code="CIAAAA", x=29, refresh=False)

    def _is_alarm_set(self):
        msg = self.bus.wait_for_response(Message("private.mycroftai.has_alarm"))
        return msg and msg.data.get("active_alarms", 0) > 0

    def display_gui(self, display_time):
        """ Display time on the Mycroft GUI. """
        self.gui.clear()
        self.gui['time_string'] = display_time
        self.gui['ampm_string'] = ''
        self.gui['date_string'] = self.get_display_date()
        self.gui.show_page('time.qml')

    def _is_display_idle(self):
        # check if the display is being used by another skill right now
        # or _get_active() == "TimeSkill"
        return self.enclosure.display_manager.get_active() == ''

    def update_display(self, force=False):
        # Don't show idle time when answering a query to prevent
        # overwriting the displayed value.
        if self.answering_query:
            return

        self.gui['time_string'] = self.get_display_current_time()
        self.gui['date_string'] = self.get_display_date()
        self.gui['ampm_string'] = '' # TODO

        if self.settings.get("show_time", False):
            # user requested display of time while idle
            if (force is True) or self._is_display_idle():
                current_time = self.get_display_current_time()
                if self.displayed_time != current_time:
                    self.displayed_time = current_time
                    self.display(current_time)
                    # return mouth to 'idle'
                    self.enclosure.display_manager.remove_active()
            else:
                self.displayed_time = None  # another skill is using display
        else:
            # time display is not wanted
            if self.displayed_time:
                if self._is_display_idle():
                    # erase the existing displayed time
                    self.enclosure.mouth_reset()
                    # return mouth to 'idle'
                    self.enclosure.display_manager.remove_active()
                self.displayed_time = None

    @intent_file_handler("what.time.is.it.intent")
    def handle_query_time_alt(self, message):
        self.handle_query_time(message)

    def _extract_location(self, utt):
        # if "Location" in message.data:
        #     return message.data["Location"]
        rx_file = self.find_resource('location.rx', 'regex')
        if rx_file:
            with open(rx_file) as f:
                for pat in f.read().splitlines():
                    pat = pat.strip()
                    if pat and pat[0] == "#":
                        continue
                    res = re.search(pat, utt)
                    if res:
                        try:
                            return res.group("Location")
                        except IndexError:
                            pass
        return None

    ######################################################################
    ## Time queries / display

    @intent_handler(IntentBuilder("").require("Query").require("Time").
                    optionally("Location"))
    def handle_query_time(self, message):
        utt = message.data.get('utterance', "")
        location = self._extract_location(utt)
        current_time = self.get_spoken_current_time(location)
        if not current_time:
            return

        # speak it
        self.speak_dialog("time.current", {"time": current_time})

        # and briefly show the time
        self.answering_query = True
        self.enclosure.deactivate_mouth_events()
        self.display(self.get_display_current_time(location))
        time.sleep(5)
        mycroft.audio.wait_while_speaking()
        self.enclosure.mouth_reset()
        self.enclosure.activate_mouth_events()
        self.answering_query = False
        self.displayed_time = None

    @intent_file_handler("what.time.will.it.be.intent")
    def handle_query_future_time(self, message):
        utt = normalize(message.data.get('utterance', "").lower())
        extract = extract_datetime(utt)
        if extract:
            dt = extract[0]
            utt = extract[1]
        location = self._extract_location(utt)
        future_time = self.get_spoken_current_time(location, dt, True)
        if not future_time:
            return

        # speak it
        self.speak_dialog("time.future", {"time": future_time})

        # and briefly show the time
        self.answering_query = True
        self.enclosure.deactivate_mouth_events()
        self.display(self.get_display_current_time(location, dt))
        time.sleep(5)
        mycroft.audio.wait_while_speaking()
        self.enclosure.mouth_reset()
        self.enclosure.activate_mouth_events()
        self.answering_query = False
        self.displayed_time = None

    @intent_handler(IntentBuilder("").require("Display").require("Time").
                    optionally("Location"))
    def handle_show_time(self, message):
        self.display_tz = None
        utt = message.data.get('utterance', "")
        location = self._extract_location(utt)
        if location:
            tz = self.get_timezone(location)
            if not tz:
                self.speak_dialog("time.tz.not.found", {"location": location})
                return
            else:
                self.display_tz = tz
        else:
            self.display_tz = None

        # show time immediately
        self.settings["show_time"] = True
        self.update_display(True)

    ######################################################################
    ## Date queries

    @intent_handler(IntentBuilder("").require("Query").require("Date").
                    optionally("Location"))
    def handle_query_date(self, message):
        utt = message.data.get('utterance', "").lower()
        extract = extract_datetime(utt)
        day = extract[0]

        # check if a Holiday was requested, e.g. "What day is Christmas?"
        year = extract_number(utt)
        if not year or year < 1500 or year > 3000:  # filter out non-years
            year = day.year
        all = {}
        # TODO: How to pick a location for holidays?
        for st in holidays.US.STATES:
            l = holidays.US(years=[year], state=st)
            for d, name in l.items():
                if not name in all:
                    all[name] = d
        for name in all:
            d = all[name]
            # Uncomment to display all holidays in the database
            # self.log.info("Day, name: " +str(d) + " " + str(name))
            if name.replace(" Day", "").lower() in utt:
                day = d
                break

        location = self._extract_location(utt)
        if location:
            # TODO: Timezone math!
            today = to_local(now_utc())
            if day.year == today.year and day.month == today.month and day.day == today.day:
                day = now_utc()  # for questions like "what is the day in sydney"
            day = self.get_local_datetime(location, dtUTC=day)
        if not day:
            return  # failed in timezone lookup

        speak = nice_date(day, lang=self.lang)
        # speak it
        self.speak_dialog("date", {"date": speak})

        # and briefly show the date
        self.answering_query = True
        self.show_date(location, day=day)
        time.sleep(10)
        mycroft.audio.wait_while_speaking()
        if self.platform == "mycroft_mark_1":
            self.enclosure.mouth_reset()
            self.enclosure.activate_mouth_events()
        self.answering_query = False
        self.displayed_time = None

    def show_date(self, location, day=None):
        if self.platform == "mycroft_mark_1":
            self.show_date_mark1(location, day)
        self.show_date_gui(location, day)

    def show_date_mark1(self, location, day):
        show = self.get_display_date(day, location)
        self.enclosure.deactivate_mouth_events()
        self.enclosure.mouth_text(show)

    def get_weekday(self, day=None, location=None):
        if not day:
            day = self.get_local_datetime(location)
        return day.strftime("%A")

    def get_month_date(self, day=None, location=None):
        if not day:
            day = self.get_local_datetime(location)
        return day.strftime("%B %d")

    def get_year(self, day=None, location=None):
        if not day:
            day = self.get_local_datetime(location)
        return day.strftime("%Y")

    def show_date_gui(self, location, day):
        self.gui.clear()
        self.gui['date_string'] = self.get_display_date(day, location)
        self.gui['weekday_string'] = self.get_weekday(day, location)
        self.gui['month_string'] = self.get_month_date(day, location)
        self.gui['year_string'] = self.get_year(day, location)
        self.gui.show_page('date.qml')


def create_skill():
    return TimeSkill()
