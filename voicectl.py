import azure.cognitiveservices.speech as speechsdk
import unidecode
import string
import time
import threading
import re
import pyaudio
import json
from vosk import Model, KaldiRecognizer


class CommandEntry:
	def __init__(self, pattern, cb):
		self.__callback = cb
		self.__pattern = pattern
		self.__varnames = {}
		self.__can_chain = False
		
		re_pattern = []
		words = pattern.split()
		words_to_skip = 0
		current_group = 0
		for idx, word in enumerate(words):
			if words_to_skip:
				words_to_skip -= 1
				continue
			if word.startswith("$"):
				word = word[1:]
				if word.endswith(")"):
					opening_loc = word.find("(")
					re_pattern.append(word[opening_loc:])
					word = word[:opening_loc]
				elif word.endswith("..."):
					word = word[:-3]
					re_pattern.append(r"(.*)")
				else:
					re_pattern.append(r"(\w+)")
				self.__varnames[word] = current_group
				current_group += 1
			elif word.startswith("("):
				current_group += 1
				if word.endswith(")"):
					re_pattern.append(word)
					continue
				idx += 1
				words_to_skip += 1
				while not words[idx].endswith(")"):
					word += " " + words[idx]
					idx += 1
					words_to_skip += 1
				word += " " + words[idx]
				re_pattern.append(word)
			else:
				re_pattern.append(word)
				
		if re_pattern[-1] != "(.*)":
			self.__can_chain = True
			re_pattern.append("?(?:and then(?P<next_command>.*))?")
			
		print(re_pattern)
			
		self.__regex = re.compile(" ".join(re_pattern), re.IGNORECASE)
		
	def match(self, expr):
		return self.__regex.fullmatch(expr)
		
	def create_kwargs(self, expr):
		match = self.match(expr)
		if match is None:
			return None, None
		groups = match.groups()
		result = {}
		for varname in self.__varnames:
			result[varname] = groups[self.__varnames[varname]]
			
		if self.__can_chain:
			nextc = match.group("next_command")
			if nextc:
				return result, nextc[1:]
			else:
				return result, None
		else:
			return result, None

		
	def try_invoke(self, expr):
		kwargs, next_command = self.create_kwargs(expr)
		if kwargs is None:
			return False, None
		
		self.__callback(**kwargs)
		return True, next_command
		

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

		self.__lq_recognizer = KaldiRecognizer(Model("model"), 16000, self.__keyword + " [unk]")
		
		self.on_ready = lambda *x: x
		self.on_triggered = lambda *x: x
		self.on_begin_command = lambda *x: x
		self.on_finish_command = lambda *x: x
		self.on_unknown_command = lambda *x: x
		self.on_error = lambda *x: x
		
		self.__commands = []
		self.next_activation = time.time()
		self.__active = False
		
	def add_command(self, pattern, callback):
		self.__commands.append(CommandEntry(pattern, callback))
		
	def perform_all_commands(self, cmd):
		while True:
			has_match = False
			for command in self.__commands:
				result, next_command = command.try_invoke(cmd)
				if result:
					has_match = True
					if next_command:
						cmd = next_command
					else:
						return
			if not has_match:
				break
		self.on_unknown_command(cmd)
		
	def listen_for_command(self):
		self.__active = True
		self.on_triggered()
		speech = self.__hq_recognizer.recognize_once().text.translate(str.maketrans('', '', string.punctuation))
		
		speech = speech.replace("please", "").replace("Please", "").strip()
		print(speech)
		self.perform_all_commands(speech)
		self.__active = False


	def start_listening(self):
		p = pyaudio.PyAudio()
		stream = p.open(format=pyaudio.paInt16, channels=1, rate=16000, input=True, frames_per_buffer=8000)
		stream.start_stream()
		self.on_ready()
		while True:
			data = stream.read(4000)
			if len(data) == 0:
				break
			if self.__lq_recognizer.AcceptWaveform(data):
				result = self.__lq_recognizer.Result()
				if not self.__active and json.loads(result)["text"] == self.__keyword:
					if self.next_activation > time.time():
						continue
					self.__active = True
					t = threading.Thread(target=self.listen_for_command)
					t.daemon = True
					t.start()
					self.next_activation = time.time() + 5
