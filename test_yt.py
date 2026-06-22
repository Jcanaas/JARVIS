import time
import actions.ytmusic_headless as hl
hl._start_mpv()
time.sleep(0.5)
hl._send_command(["loadfile","https://music.youtube.com/watch?v=dQw4w9WgXcQ","replace"])
time.sleep(5)
for prop in ["time-pos","pause","paused-for-cache","cache-buffering-state","core-idle","idle-active","playback-time","eof-reached","duration","media-title"]:
    val = hl._get_mpv_property(prop)
    print("{}  =  {}".format(prop, val))
