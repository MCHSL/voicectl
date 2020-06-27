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
				
		print(re_pattern)
		self.__regex = re.compile(" ".join(re_pattern), re.IGNORECASE)
		
	def match(self, expr):
		return self.__regex.fullmatch(expr)
		
	def create_kwargs(self, expr):
		match = self.match(expr)
		if match is None:
			return None
		groups = match.groups()
		result = {}
		for varname in self.__varnames:
			result[varname] = groups[self.__varnames[varname]]
			
		return result
		
	def try_invoke(self, expr):
		kwargs = self.create_kwargs(expr)
		if kwargs is None:
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
		
	def listen_for_command(self):
		self.__active = True
		self.on_triggered()
		speech = self.__hq_recognizer.recognize_once().text.translate(str.maketrans('', '', string.punctuation))
		
		speech = speech.replace("please", "").replace("Please", "").strip()
		print(speech)

		for command in self.__commands:
			if command.try_invoke(speech):
				self.__active = False
				return
				
		self.__active = False
		self.on_unknown_command(speech)
		
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
