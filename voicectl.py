import azure.cognitiveservices.speech as speechsdk
import unidecode
import string
import time
import threading
import re
import pyaudio
import json
from vosk import Model, KaldiRecognizer
import numpy as np
import wave

# https://scimusing.wordpress.com/2013/10/25/ring-buffers-in-pythonnumpy/
class RingBuffer():
    "A 1D ring buffer using numpy arrays"
    def __init__(self, length):
        self.data = np.zeros(length, dtype=np.int16)
        self.index = 0

    def extend(self, x):
        "adds array x to ring buffer"
        x_index = (self.index + np.arange(x.size)) % self.data.size
        self.data[x_index] = x
        self.index = x_index[-1] + 1

    def get(self, offset):
        "Returns the first-in-first-out data in the ring buffer"
        idx = (self.index + np.arange(self.data.size) - offset) %self.data.size
        return self.data[idx]


class CommandEntry:
	def __init__(self, pattern, cb, alternative_words):
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
					re_pattern.append(r"(.*?)")
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
				alts = alternative_words.get(word, None)
				if alts:
					word = "(" + word + "|"
					word += "|".join(alts) + ")"
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

WAITING_FOR_WAKEWORD = 0
RECORDING_COMMAND = 1

class VoiceController:
	def __init__(self,
	             api_key,
	             wake_word="computer",
	             region="eastus",
	             languages=None):
		self.__api_key = api_key
		self.__wake_word = wake_word
		self.__region = region
		self.__languages = languages
		if not self.__languages:
			self.__languages = ["en-US"]

		# Are we currently processing a command?
		self.__active = False

		# Samples per second to read from mic (single channel)
		self.__framerate = 44100

		self.__config = speechsdk.SpeechConfig(subscription=self.__api_key,
		                                       region=self.__region)

		# Write sounds to this stream to send it to azure's speech recognition
		self.__hq_stream = speechsdk.audio.PushAudioInputStream(stream_format = speechsdk.audio.AudioStreamFormat(samples_per_second=self.__framerate))
		self.__audio_config = speechsdk.AudioConfig(stream = self.__hq_stream)

		if len(self.__languages) == 1:
			self.__hq_recognizer = speechsdk.SpeechRecognizer(
			    speech_config=self.__config, language=self.__languages[0], audio_config = self.__audio_config)
		else:
			self.__hq_recognizer = speechsdk.SpeechRecognizer(
			    speech_config=self.__config,
			    auto_detect_source_language_config=speechsdk.languageconfig.
			    AutoDetectSourceLanguageConfig(languages=self.__languages), audio_config = self.__audio_config)

		# Recognizer used to detect the wake word
		self.__lq_recognizer = KaldiRecognizer(Model("model"), self.__framerate,'["' + self.__wake_word + '"]')

		# Add callbacks to azure events
		self.__hq_recognizer.recognized.connect(self.on_recognized)
		self.__hq_recognizer.session_stopped.connect(self.on_session_stopped)
		self.__hq_recognizer.canceled.connect(self.on_session_stopped)

		# Callbacks
		self.on_ready = lambda *x: x
		self.on_triggered = lambda *x: x
		self.on_begin_command = lambda *x: x
		self.on_finish_command = lambda *x: x
		self.on_unknown_command = lambda *x: x
		self.on_error = lambda *x: x

		# List of commands
		self.__commands = []
		self.__alternatives = {}

		self.__buffer_size = 5
		self.wake_buffer = RingBuffer(self.__buffer_size * self.__framerate)

		self.recognized = threading.Event()
		self.mode = WAITING_FOR_WAKEWORD

		# Future returned by Azure's recognize_once_async, not sure what the point is since you can just
		# connect callbacks
		self.fut = None

		# Number of frames missed by the local recognizer while processing commands
		self.missed_frames = 0

	def on_session_stopped(self, evt):
		if self.fut:
			self.fut.get()
		self.fut = None

	def add_command(self, pattern, callback):
		self.__commands.append(CommandEntry(pattern, callback, self.__alternatives))

	def add_alternatives(self, word_or_dict, alts=[]):
		if type(word_or_dict) == dict:
			self.__alternatives.update(word_or_dict)
		else:
			if word_or_dict in self.__alternatives:
				self.__alternatives[word_or_dict] += alts
			else:
				self.__alternatives[word_or_dict] = alts

	def perform_all_commands(self, cmd):
		while True:
			has_match = False
			for command in self.__commands:
				result, next_command = command.try_invoke(cmd)
				if result:
					has_match = True
					if next_command:
						cmd = next_command
						break
					else:
						return
			if not has_match:
				break
		self.on_unknown_command(cmd)

	def on_recognized(self, event):
		try:
			speech = event.result.text.translate(str.maketrans('', '', string.punctuation)).lower()
			print("Recognized: {}".format(speech))
			self.perform_all_commands(speech)
			self.fut.get()
			self.fut = None

			self.mode = WAITING_FOR_WAKEWORD
		except Exception as e:
			print(e)

	def audio_callback(self, in_data, frame_count, time_info, status):
		if status:
			print(status)

		audio_data = np.fromstring(in_data, dtype=np.int16)
		if self.mode == WAITING_FOR_WAKEWORD:
			self.wake_buffer.extend(audio_data)
			if self.__lq_recognizer.AcceptWaveform(in_data):
				self.recognized.set()
		elif self.mode == RECORDING_COMMAND:
			self.__hq_stream.write(audio_data)
			self.missed_frames += frame_count


		return (None, pyaudio.paContinue)

	def reset_offline_recognizer(self):
		self.missed_frames = 0
		self.__lq_recognizer = KaldiRecognizer(Model("model"), self.__framerate,'["' + self.__wake_word + '"]')

	def recognize_stream(self):
		self.start_time = time.time()
		while True:
			self.recognized.wait()
			self.recognized.clear()

			result = self.__lq_recognizer.Result()

			jres = json.loads(result)
			if not self.__active and jres["text"] == self.__wake_word:
				self.on_triggered()
				self.mode = RECORDING_COMMAND

				wakeword_end_time = 0
				for res in jres["result"]:
					if res["word"] == self.__wake_word:
						wakeword_end_time = res["end"]

				lag = time.time() - self.start_time - wakeword_end_time - (self.missed_frames / self.__framerate)
				lag = int(round((lag) * self.__framerate))

				start_data = self.wake_buffer.get(lag)
				self.fut = self.__hq_recognizer.recognize_once_async()
				missed = start_data[:lag]
				missed = np.resize(missed, self.__framerate)
				self.__hq_stream.write(missed)


	def start_listening(self):
		p = pyaudio.PyAudio()
		stream = p.open(format=pyaudio.paInt16,
		                channels=1,
		                rate=self.__framerate,
		                input=True,
		                frames_per_buffer=1024,
						stream_callback=self.audio_callback)
		stream.start_stream()
		self.on_ready()
		self.recognize_stream()
