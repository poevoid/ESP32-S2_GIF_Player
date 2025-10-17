# ESP32-S2_GIF_Player
Play gifs on an SH1106 display using the lolin S2 mini, a very cheap dev board. To use it you will have to successfully flash circuitpython 9.x to the device first, then just copy this repo to CIRCUITPY. GIFs must be black and white only, with 128x64px resolution. Can also tell time with a wifi connection.

Can now play audio as well! Use a single transistor amplifier on pin 18 with a low pass filter, feed the amped signal to a speaker, and we've got crunchy lovely sound. Playys slows when trying to do gifs at the same time, but works great on its own so both modes are now available separately. I like BMO. Hypnotoad is pretty neat, too, like, what's up with him, yk? Also threw in a flashing rgb led and a uv led for party vibes. 
