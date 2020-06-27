# voicectl
Alexa/Google Assistant/Voice whatever clone

# Installation
This library requires Python 3.8, 64-bit.  
The requirements file does not include PyAudio due to compilation problems. Install it separately. If you encounter issues you can install the .whl file from here: https://www.lfd.uci.edu/~gohlke/pythonlibs/#pyaudio. Make sure you download the right wheel file, add the time of writing the name is `PyAudio‑0.2.11‑cp38‑cp38‑win_amd64.whl`

# What is this?
Voicectl is a library for creating a voice assistant, similar to Amazon's Alexa. You can add voice commands that when matched, trigger python functions.

# Example
```python
import voicectl

def greet(name):
  print(f"Hello, {name}!")
 
controller = voicectl.VoiceController("AZURE API KEY GOES HERE", keyword="alexa")
controller.add_command("say hello to $name", greet)
controller.start_listening()
```

In this example we add a command that greets a certain person. The first argument to controller.add_command is the string that when spoken, will trigger the function passed as the second argument.  
You can use variables in command strings to enable the users to pass arguments. Variables in command strings are prefixed with `$`. They are passed as keyword arguments to callbacks.  
In the example, if the user says "Say hello to Adam", `greet` will be called with the argument `Adam`.

Current ways of capturing arguments:
| Command string                                                     | Spoken phrase                                | Arguments                                               | Comment                                                                                           |
|--------------------------------------------------------------------|----------------------------------------------|---------------------------------------------------------|---------------------------------------------------------------------------------------------------|
| "$name is cool"                                                    | "Adam is cool"                               | name = "Adam"                                           |                                                                                                   |
| "play $song..."                                                    | "Play something nice"                        | song = "something nice"                                 |                                                                                                   |
| "play $song... on youtube"                                         | "Play something cool on youtube"             | song = "something cool"                                 |                                                                                                   |
| "play &song... on $service"                                        | "Play despacito 2 on discord."               | song = "despacito 2", service = "discord"               |                                                                                                   |
| "(nevermind\|never mind)"                                          | "Nevermind"                                  | -                                                       | The user can say one of any of the phrases in parentheses.                                        |
| "turn $dir(up\|down) the volume"                                   | "Turn down the volume"                       | dir = "down"                                            | The user must say one of the phrases in parentheses. The chosen word is passed in as an argument. |
| "buy $who $what... while you are at $restaurant(kfc\|burger king)" | "Buy me some hot wings while you are at KFC" | who = "me", what = "some hot wings", restaurant = "KFC" |                                                                                                   |
