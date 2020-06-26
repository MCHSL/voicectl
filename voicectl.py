import azure.cognitiveservices.speech as speechsdk
import speech_recognition as sr
import unidecode
import string
import time
from pocketsphinx import LiveSpeech
import threading
import re


class CommandEntry:
	def __init__(self, pattern, cb):
		self.__callback = cb
		self.__pattern = pattern
		self.__varnames = []
		
		re_pattern = []
		words = pattern.split()
		for word in words:
			if word.startswith("$"):
				if word.endswith("..."):
					word = word[:-3]
					re_pattern.append(r"(.*)")
				else:
					re_pattern.append(r"(\w+)")
				self.__varnames.append(word[1:])
			else:
				re_pattern.append(word)
				
		self.__regex = re.compile(" ".join(re_pattern), re.IGNORECASE)
		
	def match(self, expr):
		return self.__regex.fullmatch(expr)
		
	def create_kwargs(self, expr):
		match = self.match(expr)
		if not match:
			return None
		groups = match.groups()
		result = {}
		for varname, group in zip(self.__varnames, groups):
			result[varname] = group
			
		return result
		
	def try_invoke(self, expr):
		kwargs = self.create_kwargs(expr)
		if not kwargs:
			return False
		
		self.__callback(**kwargs)
		return True
		

class VoiceController:
	def __init__(self, api_key, keyword="computer", region="westus", languages=None):
		self.__api_key = api_key
		self.__keyword = keyword
		self.__region = region
		self.__languages = languages
		if not self.__languages:
			self.__languages = ["en-US"]
			
		self.__config = speechsdk.SpeechConfig(subscription=self.__api_key, region=self.__region)
		if len(self.__languages) == 1:
			self.__hq_recognizer = speechsdk.SpeechRecognizer(speech_config=self.__config, language=self.__languages[0])
		else:
			self.__hq_recognizer = speechsdk.SpeechRecognizer(speech_config=self.__config, auto_detect_source_language_config=speechsdk.languageconfig.AutoDetectSourceLanguageConfig(languages=self.__languages))
			
		self.__microphone = sr.Microphone()
		self.__lq_recognizer = sr.Recognizer()
		
		self.on_ready = lambda *x: x
		self.on_triggered = lambda *x: x
		self.on_begin_command = lambda *x: x
		self.on_finish_command = lambda *x: x
		self.on_unknown_command = lambda *x: x
		self.on_error = lambda *x: x
		
		self.__commands = []
		
	def add_command(self, pattern, callback):
		self.__commands.append(CommandEntry(pattern, callback))
		
	def listen_for_command(self):
		self.on_triggered()
		speech = self.__hq_recognizer.recognize_once().text.translate(str.maketrans('', '', string.punctuation))

		for command in self.__commands:
			if command.try_invoke(speech):
				break
		
	def on_audio(self, recognizer, audio):
		try:
			text = self.__lq_recognizer.recognize_sphinx(audio, keyword_entries=[(self.__keyword, 1.0)])
		except sr.UnknownValueError:
			return
		if text.lower().strip() == self.__keyword:
			t = threading.Thread(target=self.listen_for_command)
			t.daemon = True
			t.start()


	def start_listening(self):
		with self.__microphone as source:
			self.__lq_recognizer.adjust_for_ambient_noise(source)
		stop_listening = self.__lq_recognizer.listen_in_background(self.__microphone, self.on_audio, phrase_time_limit=2.0)
		self.on_ready()
		while True:
			time.sleep(100)
