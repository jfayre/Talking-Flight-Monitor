#!/usr/bin/python
# -*- coding: iso-8859-15 -*-
#==============================================================================
# Voice Flight Following - Periodically announce cities along your flight path
# Copyright (C) 2019 by Jason Fayre
# based on the VoiceATIS addon by   Oliver Clemens
# 
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.
# 
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE.	 See the GNU General Public License for more
# details.
# 
# You should have received a copy of the GNU General Public License along with
# this program.	 If not, see <https://www.gnu.org/licenses/>.
#==============================================================================

from __future__ import division


import json
import logging
# Import built-ins
import os
import re
import sys
import time
import warnings
import winsound
from configparser import ConfigParser

from contextlib import closing
from math import *

import keyboard
import lucia
import pyttsx3
import requests
from aviationFormula.aviationFormula import calcBearing
from babel import Locale
from babel.dates import get_timezone, get_timezone_name
from lucia.utils import timer

# Import own packages.
from VaLogger import VaLogger

# initialize the log settings
logging.basicConfig(filename = 'error.log', level = logging.INFO)
# Set encoding
#reload(sys)
#sys.setdefaultencoding('iso-8859-15')  # @UndefinedVariable

# Import pip packages (except failure with debug mode).
try:
	import pyuipc
	pyuipcImported = True
	debug = False
except ImportError:
		pyuipcImported = False
		debug = True

## Main Class of FlightFollowing.
# Run constructor to run the program.
class FlightFollowing(object):
#  - b: a 1-byte unsigned value, to be converted into a Python int
#  - c: a 1-byte signed value, to be converted into a Python int
#  - h: a 2-byte signed value, to be converted into a Python int
#  - H: a 2-byte unsigned value, to be converted into a Python int
#  - d: a 4-byte signed value, to be converted into a Python int
#  - u: a 4-byte unsigned value, to be converted into a Python long
#  - l: an 8-byte signed value, to be converted into a Python long
#  - L: an 8-byte unsigned value, to be converted into a Python long
#  - f: an 8-byte floating point value, to be converted into a Python double

	OFFSETS = [(0x034E,'H'),	# com1freq
			   (0x3118,'H'),	# com2freq
			   (0x3122,'b'),	# radioActive
			   (0x0560,'l'),	# ac Latitude
			   (0x0568,'l'),	# ac Longitude
			   (0x30f0,'h'),	# flaps angle
			   (0x0366,'h'),	# on ground flag: 0 = airborne
			   (0x0bc8,'h'),	# parking Brake: 0 off, 32767 on
			   (0x0574,'u'),	#ASL altitude
			   (0x0020,'u'),	# ground altitude x 256
			   (0x0bcc,'u'),	# spoilers armed: 0 - off, 1 - armed
			  ]
	## Setup the FlightFollowing object.
	# Also starts the voice generation loop.
	def __init__(self,**optional):
		#TODO: Remove the debug code when tested properly.
		
		# Process optional arguments.
		self.debug = optional.get('Debug',debug)
		
		# Get file path.
		self.rootDir = os.path.abspath(os.path.dirname(sys.argv[0]))
		
		# Init logging.
		self.logger = VaLogger(os.path.join(self.rootDir,'voiceAtis','logs'))
		# init config parser
		self.config = ConfigParser()
		# First log message.
		self.logger.info('Flight Following started')
		# check for config file. Create it if it doesn't exist.
		exists = os.path.isfile(self.rootDir + "/flightfollowing.ini")
		if exists:
			self.logger.info("config file exists.")
			cfgfile = self.config.read(self.rootDir + "/flightfollowing.ini")
			self.geonames_username = self.config.get('config','geonames_username')
			self.interval = float(self.config.get('config','interval'))
			self.distance_units = self.config.get('config','distance_units')
			self.voice_rate = self.config.get('config','voice_rate')
			
			
		else:
			self.logger.info ("no config file found. It will be created.")
			cfgfile = open (self.rootDir + "/flightfollowing.ini",'w')
			self.config.add_section ('config')
			self.config.set ('config','geonames_username','your_user_name')
			self.config.set ('config','voice_rate','150')
			self.config.set ('config','interval','180')
			self.config.set ('config','distance_units','0')
			
			self.config.write(cfgfile)
			cfgfile.close()
			
		# Establish pyuipc connection
		while True:
			try:
				self.pyuipcConnection = pyuipc.open(0)
				self.pyuipcOffsets = pyuipc.prepare_data(self.OFFSETS)
				self.logger.info('FSUIPC connection established.')
				break
			except NameError:
				self.pyuipcConnection = None
				self.logger.warning('Using voiceAtis without FSUIPC.')
				break
			except:
				self.logger.warning('FSUIPC: No simulator detected. Start your simulator first! Retrying in 20 seconds.')
				time.sleep(20)
		
		# Show debug Info
		#TODO: Remove for release.
		if self.debug:
			self.logger.info('Debug mode on.')
			self.logger.setLevel(ConsoleLevel='debug')
		## add global hotkey definitions
		keyboard.add_hotkey('ctrl+alt+c', self.AnnounceInfo)
		self.commandKey = keyboard.add_hotkey(']', self.commandMode, args=(), suppress=True, timeout=2)
		# variables to track states of various aircraft instruments
		self.oldTz = 'none' ## variable for storing timezone name
		self.old_flaps = 0
		self.airborne = False
		self.oldBrake = True
		self.oldCom1 = None
		self.oldSpoilers = None
		# start timers
		self.tmrCity = timer.Timer()
		self.tmrInstruments = timer.Timer()
		self.AnnounceInfo()
		
		
		# Infinite loop.
		try:
			while True:
				self.getPyuipcData()
				if self.tmrCity.elapsed > (self.interval * 60 * 1000):
					self.AnnounceInfo()
					self.tmrCity.restart()
				if self.tmrInstruments.elapsed > 500:
					self.readInstruments()
					self.tmrInstruments.restart()
				time.sleep(0.05)
				pass
				
		except KeyboardInterrupt:
			# Actions at Keyboard Interrupt.
			self.logger.info('Loop interrupted by user.')
			if pyuipcImported:
				self.pyuipc.close()
		
	## handle hotkeys for reading instruments on demand
	def keyHandler(self, instrument):
		if instrument == '1':
			lucia.output.output(f'{self.ASLAltitude} feet A S L')
			keyboard.remove_hotkey(self.aslKey)
			keyboard.remove_hotkey(self.aglKey)
			
			
		elif instrument == '2':
			AGLAltitude = self.ASLAltitude - self.groundAltitude
			lucia.output.output(F"{round(AGLAltitude)} feet A G L")
			keyboard.remove_hotkey(self.aglKey)
			keyboard.remove_hotkey(self.aslKey)
			
			
			

			
	## Layered key support for reading various instrumentation
	def commandMode(self):
		self.aslKey= keyboard.add_hotkey ('a', self.keyHandler, args=('1'), suppress=True, timeout=2)
		self.aglKey = keyboard.add_hotkey ('g', self.keyHandler, args=('2'), suppress=True, timeout=2)
		winsound.Beep(500, 100)
		
	## read various instrumentation automatically such as flaps
	def readInstruments(self):
		flapsTransit = False
		# Get data from simulator
		# detect if aircraft is on ground or airborne.
		if not self.onGround and not self.airborne:
			lucia.output.output ("Positive rate.")
			
			self.airborne = True
		# read parking Brakes
		
		if self.oldBrake != self.parkingBrake:
			if self.parkingBrake:
				lucia.output.output ("parking Brake on.")
				self.oldBrake = self.parkingBrake
			else:
				lucia.output.output ("parking Brake off.")
				self.oldBrake = self.parkingBrake

		
		
		# if flaps position has changed, flaps are in motion. We need to wait until they have stopped moving to read the value.
		if self.flaps != self.old_flaps:
			flapsTransit = True
			while flapsTransit:
				self.getPyuipcData()
				if self.flaps != self.old_flaps:
					self.old_flaps = self.flaps
					time.sleep (0.2)
				else:
					flapsTransit = False
			lucia.output.output (F'Flaps {self.flaps:.0f}')
			self.old_flaps = self.flaps
		# announce radio frequency changes
		if self.com1frequency != self.oldCom1:
			lucia.output.output (F"com 1, {self.com1frequency}")
			self.oldCom1 = self.com1frequency
		# spoilers
		if self.spoilers == 1 and self.oldSpoilers != self.spoilers:
			lucia.output.output ("spoilers armed.")
			self.oldSpoilers = self.spoilers
			

	## Announce flight following info
	def AnnounceInfo(self):
		# Get data from simulator
		self.getPyuipcData()
		# Lookup nearest cities to aircraft position using the Geonames database.
		self.airport="test"
		try:
			response = requests.get('http://api.geonames.org/findNearbyPlaceNameJSON?style=long&lat={}&lng={}&username={}&cities=cities5000&radius=200'.format(self.lat,self.lon, self.geonames_username))
			response.raise_for_status() # throw an exception if we get an error from Geonames.
			data =response.json()
			if len(data['geonames']) >= 1:
				bearing = calcBearing (self.lat, self.lon, float(data["geonames"][0]["lat"]), float(data["geonames"][0]["lng"]))
				bearing = (degrees(bearing) +360) % 360
				if self.distance_units == '1':
					distance = float(data["geonames"][0]["distance"]) / 1.609
					units = 'miles'
				else:
					distance = float(data["geonames"][0]["distance"])
					units = 'kilometers'
				lucia.output.output ('Closest city: {} {}. {:.1f} {}. Bearing: {:.0f}'.format(data["geonames"][0]["name"],data["geonames"][0]["adminName1"],distance,units,bearing))
			else:
				distance = 0
		except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
			logging.error('latitude:{}, longitude:{}'.format(self.lat, self.lon))
			logging.exception('error getting nearest city: ' + str(e))
			lucia.output.output ('cannot find nearest city. Geonames connection error. Check error log.')
		except requests.exceptions.HTTPError as e:
			logging.error('latitude:{}, longitude:{}'.format(self.lat, self.lon))
			logging.exception('error getting nearest city. Error while connecting to Geonames.' + str(e))
			lucia.output.output ('cannot find nearest city. Geonames may be busy. Check error log.')
			
		## Check if we are flying over water.
		## If so, announce body of water.
		## We will continue to announce over water until the maximum radius of the search is reached.
		try:
			response = requests.get('http://api.geonames.org/oceanJSON?lat={}&lng={}&username={}'.format(self.lat,self.lon, self.geonames_username))
			data = response.json()
			if 'ocean' in data and distance >= 1:
				lucia.output.output ('currently over {}'.format(data['ocean']['name']))
				self.oceanic = True
		except Exception as e:
			logging.error('Error determining oceanic information: ' + str(e))
			logging.exception(str(e))
			sys.exit()
			
		## Read time zone information
		try:
			response = requests.get('http://api.geonames.org/timezoneJSON?lat={}&lng={}&username={}'.format(self.lat,self.lon, self.geonames_username))
			data = response.json()
			
			if 'timezoneId' in data:
				tz = get_timezone(data['timezoneId'])
				tzName = get_timezone_name(tz, locale=Locale.parse('en_US'))
				if tzName != self.oldTz:
					lucia.output.output ('{}.'.format(tzName))
					self.oldTz = tzName
		except Exception as e:
			logging.error('Error determining timezone: ' + str(e))
			logging.exception(str(e))
			sys.exit()


	
	## Read data from the simulator
	def getPyuipcData(self):
		
		if pyuipcImported:
			results = pyuipc.read(self.pyuipcOffsets)
			hexCode = hex(results[0])[2:]
			self.com1frequency = float('1{}.{}'.format(hexCode[0:2],hexCode[2:]))
			hexCode = hex(results[1])[2:]
			self.com2frequency = float('1{}.{}'.format(hexCode[0:2],hexCode[2:]))
			# lat lon
			self.lat = results[3] * (90.0/(10001750.0 * 65536.0 * 65536.0))
			self.lon = results[4] * (360.0/(65536.0 * 65536.0 * 65536.0 * 65536.0))
			self.flaps = results[5]/ 256
			self.onGround = bool(results[6])
			self.parkingBrake = bool(results[7])
			self.ASLAltitude = round(results[8] * 3.28084)
			self.groundAltitude = results[9] / 256 * 3.28084
			self.spoilers = results[10]			
			#breakpoint()
			
			

			
		
		else:
			self.com1frequency = self.COM1_FREQUENCY_DEBUG
			self.com2frequency = self.COM2_FREQUENCY_DEBUG
			self.com1active = True
			self.com2active = True
			self.lat = self.LAT_DEBUG
			self.lon = self.LON_DEBUG

if __name__ == '__main__':
	FlightFollowing = FlightFollowing()
	pass
