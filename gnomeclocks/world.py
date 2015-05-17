# Copyright(c) 2011-2012 Collabora, Ltd.
#
# Gnome Clocks is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the
# Free Software Foundation; either version 2 of the License, or(at your
# option) any later version.
#
# Gnome Clocks is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY
# or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License
# for more details.
#
# You should have received a copy of the GNU General Public License along
# with Gnome Clocks; if not, write to the Free Software Foundation,
# Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA
#
# Author: Seif Lotfy <seif.lotfy@collabora.co.uk>

import os
import errno
import time
import json
from gi.repository import GLib, GObject, Gio, GdkPixbuf, Gtk
from gi.repository import GWeather
from clocks import Clock
from utils import Dirs, TimeString, WallClock
from widgets import SelectableIconView, ContentView


# keep the GWeather world around as a singletom, otherwise
# if is garbage collected get_city_name etc fail.
gweather_world = GWeather.Location.new_world(True)
wallclock = WallClock.get_default()


class WorldClockStorage:
    def __init__(self):
        self.filename = os.path.join(Dirs.get_user_data_dir(), "clocks.json")

    def save(self, clocks):
        location_codes = [c.location.get_code() for c in clocks]
        f = open(self.filename, "wb")
        json.dump(location_codes, f)
        f.close()

    def load(self):
        clocks = []
        try:
            f = open(self.filename, "rb")
            location_codes = json.load(f)
            f.close()
            for l in location_codes:
                location = GWeather.Location.find_by_station_code(gweather_world, l)
                if location:
                    clock = ClockItem(location)
                    clocks.append(clock)
        except IOError as e:
            if e.errno == errno.ENOENT:
                # File does not exist yet, that's ok
                pass

        return clocks


class NewWorldClockDialog(Gtk.Dialog):
    def __init__(self, parent):
        Gtk.Dialog.__init__(self, _("Add a New World Clock"), parent)
        self.set_transient_for(parent)
        self.set_modal(True)
        self.set_size_request(400, -1)
        self.set_border_width(3)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        area = self.get_content_area()
        area.pack_start(box, True, True, 0)

        label = Gtk.Label(_("Search for a city:"))
        label.set_alignment(0.0, 0.5)

        self.entry = GWeather.LocationEntry.new(gweather_world)
        self.find_gicon = Gio.ThemedIcon.new_with_default_fallbacks(
            'edit-find-symbolic')
        self.clear_gicon = Gio.ThemedIcon.new_with_default_fallbacks(
            'edit-clear-symbolic')
        self.entry.set_icon_from_gicon(
            Gtk.EntryIconPosition.SECONDARY, self.find_gicon)
        self.entry.set_activates_default(True)

        self.add_buttons(Gtk.STOCK_CANCEL, 0, Gtk.STOCK_ADD, 1)
        self.set_default_response(1)
        self.set_response_sensitive(1, False)

        box.pack_start(label, False, False, 6)
        box.pack_start(self.entry, False, False, 3)
        box.set_border_width(5)

        self.entry.connect("activate", self._set_city)
        self.entry.connect("changed", self._set_city)
        self.entry.connect("icon-release", self._icon_released)
        self.show_all()

    def get_location(self):
        return self.entry.get_location()

    def _set_city(self, widget):
        location = self.entry.get_location()
        if self.entry.get_text() == '':
            self.entry.set_icon_from_gicon(
                Gtk.EntryIconPosition.SECONDARY, self.find_gicon)
        else:
            self.entry.set_icon_from_gicon(
                Gtk.EntryIconPosition.SECONDARY, self.clear_gicon)
        if location:
            self.set_response_sensitive(1, True)
        else:
            self.set_response_sensitive(1, False)

    def _icon_released(self, icon_pos, event, data):
        if self.entry.get_icon_gicon(
                Gtk.EntryIconPosition.SECONDARY) == self.clear_gicon:
            self.entry.set_text('')
            self.entry.set_icon_from_gicon(
                Gtk.EntryIconPosition.SECONDARY, self.find_gicon)
            self.set_response_sensitive(1, False)


class ClockItem:
    def __init__(self, location):
        self.location = location
        self.name = self._get_location_name()

        weather_timezone = self.location.get_timezone()
        timezone = GLib.TimeZone.new(weather_timezone.get_tzid())
        i = timezone.find_interval(GLib.TimeType.UNIVERSAL, wallclock.time)
        location_offset = timezone.get_offset(i)

        timezone = GLib.TimeZone.new_local()
        i = timezone.find_interval(GLib.TimeType.UNIVERSAL, wallclock.time)
        here_offset = timezone.get_offset(i)

        self.offset = location_offset - here_offset

        self.sunrise = None
        self.sunset = None
        self.sunrise_string = None
        self.sunset_string = None

        self._update_sunrise_sunset()

        self.update_time()

    def _get_location_name(self):
        nation = self.location
        while nation and nation.get_level() != GWeather.LocationLevel.COUNTRY:
            nation = nation.get_parent()
        if nation:
            return self.location.get_city_name() + ', ' + nation.get_name()
        else:
            return self.location.get_city_name()

    def _get_location_time(self, secs=None):
        if not secs:
            secs = wallclock.time
        t = secs + self.offset
        t = time.localtime(t)
        return t

    def _get_day_string(self):
        clock_time_day = self.location_time.tm_yday
        local_time_day = wallclock.localtime.tm_yday

        # if its 31st Dec here and 1st Jan there, clock_time_day = 1,
        # local_time_day = 365/366
        # if its 1st Jan here and 31st Dec there, clock_time_day = 365/366,
        # local_time_day = 1
        if clock_time_day > local_time_day:
            if local_time_day == 1:
                return _("Yesterday")
            else:
                return _("Tomorrow")
        elif clock_time_day < local_time_day:
            if clock_time_day == 1:
                return _("Tomorrow")
            else:
                return _("Yesterday")

    def _update_sunrise_sunset(self):
        self.weather = GWeather.Info(location=self.location, world=gweather_world)
        self.weather.connect('updated', self._on_weather_updated)
        self.weather.update()

    def _on_weather_updated(self, weather):
        # returned as the time here
        ok1, sunrise = weather.get_value_sunrise()
        ok2, sunset = weather.get_value_sunset()
        if ok1 and ok2:
            self.sunrise = self._get_location_time(sunrise)
            self.sunset = self._get_location_time(sunset)
            self.sunrise_string = TimeString.format_time(self.sunrise)
            self.sunset_string = TimeString.format_time(self.sunset)
            self.is_light = self._get_is_light()

    def _get_is_light(self):
        current = self.location_time
        if self.sunrise:
            return self.sunrise <= current <= self.sunset
        else:
            # default fallback when we have no sunrise/sunset times,
            # as we only have images for either day or night
            return 7 <= current.tm_hour <= 19

    def update_time(self):
        self.location_time = self._get_location_time()
        self.time_string = TimeString.format_time(self.location_time)
        self.day_string = self._get_day_string()
        self.is_light = self._get_is_light()


class ClockStandalone(Gtk.EventBox):
    def __init__(self):
        Gtk.EventBox.__init__(self)
        self.get_style_context().add_class('view')
        self.get_style_context().add_class('content-view')
        self.can_edit = False
        self.time_label = Gtk.Label()

        self.vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(self.vbox)

        time_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.time_label.set_alignment(0.0, 0.5)
        time_box.pack_start(self.time_label, True, True, 0)

        self.hbox = hbox = Gtk.Box()
        self.hbox.set_homogeneous(False)

        self.hbox.pack_start(Gtk.Label(), True, True, 0)
        self.hbox.pack_start(time_box, False, False, 0)
        self.hbox.pack_start(Gtk.Label(), True, True, 0)

        self.vbox.pack_start(Gtk.Label(), True, True, 25)
        self.vbox.pack_start(hbox, False, False, 0)
        self.vbox.pack_start(Gtk.Label(), True, True, 0)

        sunrise_label = Gtk.Label()
        sunrise_label.set_markup(
            "<span size ='large' color='dimgray'>%s</span>" % (_("Sunrise")))
        sunrise_label.set_alignment(1.0, 0.5)
        self.sunrise_time_label = Gtk.Label()
        self.sunrise_time_label.set_alignment(0.0, 0.5)
        sunrise_hbox = Gtk.Box(True, 9)
        sunrise_hbox.pack_start(sunrise_label, False, False, 0)
        sunrise_hbox.pack_start(self.sunrise_time_label, False, False, 0)

        sunset_label = Gtk.Label()
        sunset_label.set_markup(
            "<span size ='large' color='dimgray'>%s</span>" % (_("Sunset")))
        sunset_label.set_alignment(1.0, 0.5)
        self.sunset_time_label = Gtk.Label()
        self.sunset_time_label.set_alignment(0.0, 0.5)
        sunset_hbox = Gtk.Box(True, 9)
        sunset_hbox.pack_start(sunset_label, False, False, 0)
        sunset_hbox.pack_start(self.sunset_time_label, False, False, 0)

        self.sunbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.sunbox.set_homogeneous(True)
        self.sunbox.set_spacing(3)
        self.sunbox.pack_start(sunrise_hbox, False, False, 3)
        self.sunbox.pack_start(sunset_hbox, False, False, 3)

        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        hbox.pack_start(Gtk.Label(), True, True, 0)
        hbox.pack_start(self.sunbox, False, False, 0)
        hbox.pack_start(Gtk.Label(), True, True, 0)
        self.vbox.pack_end(hbox, False, False, 30)

        self.set_clock(None)

    def set_clock(self, clock):
        self.clock = clock
        if clock:
            self.update()
            self.show_all()
            if not clock.sunrise:
                self.sunbox.hide()

    def get_name(self):
        return GLib.markup_escape_text(self.clock.name)

    def update(self):
        if self.clock:
            timestr = self.clock.time_string
            self.time_label.set_markup(
                "<span size='72000' color='dimgray'><b>%s</b></span>" % timestr)
            if self.clock.sunrise_string and self.clock.sunset_string:
                self.sunrise_time_label.set_markup(
                    "<span size ='large'>%s</span>" % self.clock.sunrise_string)
                self.sunset_time_label.set_markup(
                    "<span size ='large'>%s</span>" % self.clock.sunset_string)
                self.sunbox.show()
            else:
                self.sunbox.hide()


class World(Clock):
    def __init__(self):
        # Translators: "New" refers to a world clock
        Clock.__init__(self, _("World"), _("New"))

        self.notebook = Gtk.Notebook()
        self.notebook.set_show_tabs(False)
        self.notebook.set_show_border(False)
        self.add(self.notebook)

        f = os.path.join(Dirs.get_image_dir(), "cities", "day.png")
        self.daypixbuf = GdkPixbuf.Pixbuf.new_from_file(f)
        f = os.path.join(Dirs.get_image_dir(), "cities", "night.png")
        self.nightpixbuf = GdkPixbuf.Pixbuf.new_from_file(f)

        self.liststore = Gtk.ListStore(bool, str, object)
        self.iconview = SelectableIconView(self.liststore, 0, 1, self._thumb_data_func)
        self.iconview.connect("item-activated", self._on_item_activated)
        self.iconview.connect("selection-changed", self._on_selection_changed)

        contentview = ContentView(self.iconview,
                                  "document-open-recent-symbolic",
                                  _("Select <b>New</b> to add a world clock"))
        self.notebook.append_page(contentview, None)

        self.storage = WorldClockStorage()
        self.clocks = []
        self.load_clocks()
        self.show_all()

        self.standalone = ClockStandalone()
        self.notebook.append_page(self.standalone, None)

        wallclock.connect("time-changed", self._update_clocks)

    def _thumb_data_func(self, view, cell, store, i, data):
        clock = store.get_value(i, 2)
        cell.text = clock.time_string
        cell.subtext = clock.day_string
        if clock.is_light:
            cell.props.pixbuf = self.daypixbuf
            cell.css_class = "light"
        else:
            cell.props.pixbuf = self.nightpixbuf
            cell.css_class = "dark"

    def set_mode(self, mode):
        self.mode = mode
        if mode is Clock.Mode.NORMAL:
            self.notebook.set_current_page(0)
            self.iconview.set_selection_mode(False)
        elif mode is Clock.Mode.STANDALONE:
            self.notebook.set_current_page(1)
        elif mode is Clock.Mode.SELECTION:
            self.iconview.set_selection_mode(True)

    def _update_clocks(self, *args):
        for c in self.clocks:
            c.update_time()
        self.iconview.queue_draw()
        self.standalone.update()
        return True

    def _on_item_activated(self, iconview, path):
        clock = self.liststore[path][2]
        self.standalone.set_clock(clock)
        self.emit("item-activated")

    def _on_selection_changed(self, iconview):
        self.emit("selection-changed")

    @GObject.Property(type=bool, default=False)
    def can_select(self):
        return len(self.liststore) != 0

    def get_selection(self):
        return self.iconview.get_selection()

    def delete_selected(self):
        selection = self.get_selection()
        clocks = [self.liststore[path][2] for path in selection]
        self.delete_clocks(clocks)
        self.emit("selection-changed")

    def load_clocks(self):
        self.clocks = self.storage.load()
        for clock in self.clocks:
            self._add_clock_item(clock)

    def add_clock(self, location):
        if location.get_code() in [c.location.get_code() for c in self.clocks]:
            # duplicate
            return
        clock = ClockItem(location)
        self.clocks.append(clock)
        self.storage.save(self.clocks)
        self._add_clock_item(clock)
        self.show_all()

    def _add_clock_item(self, clock):
        label = GLib.markup_escape_text(clock.name)
        self.liststore.append([False, "<b>%s</b>" % label, clock])
        self.notify("can-select")

    def delete_clocks(self, clocks):
        self.clocks = [c for c in self.clocks if c not in clocks]
        self.storage.save(self.clocks)
        self.liststore.clear()
        self.load_clocks()
        self.notify("can-select")

    def open_new_dialog(self):
        window = NewWorldClockDialog(self.get_toplevel())
        window.connect("response", self._on_dialog_response)
        window.show_all()

    def _on_dialog_response(self, dialog, response):
        if response == 1:
            l = dialog.get_location()
            self.add_clock(l)
        dialog.destroy()