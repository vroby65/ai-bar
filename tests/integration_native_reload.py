"""Opt-in native test: PYTHONPATH=. xvfb-run -a python3 tests/integration_native_reload.py

Requires Openbox, Google Chrome and xdotool. Uses a disposable Chrome profile.
"""
import subprocess,tempfile,time,os,sys,json,shutil
from types import SimpleNamespace
from unittest.mock import patch
from ai_bar.app import Gtk,GLib,Wnck,release_x11_window,restore_x11_window,xlib_display,X11SuperToggle,AiBarWindow

def pump(seconds=.3):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        while GLib.MainContext.default().pending(): GLib.MainContext.default().iteration(False)
        time.sleep(.01)
if os.environ.get('CHECK_STAGE'):
    xid, cp, wp, profile_path = json.loads(os.environ['CHECK_STAGE'])
    try:
        pump(.5)
        AiBarWindow._restore_native_windows_after_reload(None)
        pump(1)
        d=xlib_display.Display();w=d.create_resource_object('window',xid)
        print('after exec restore',w.get_attributes().map_state,flush=True)
        assert w.get_attributes().map_state==2
        host=Gtk.Window(); host.set_default_size(500,400);socket=Gtk.Socket();host.add(socket);host.show_all();pump()
        while not release_x11_window(xid):pump(.1)
        socket.add_id(xid);pump()
        from unittest.mock import Mock
        controller=AiBarWindow.__new__(AiBarWindow)
        controller.embedded={'window:test':socket}
        controller.embedded_window_xids={'window:test':xid}
        monitor=X11SuperToggle(lambda:None,lambda:None,controller._focus_clicked_embedded_window)
        assert monitor.start();pump(.2)
        other=Gtk.Window();other.move(600,500);other.show_all();other.present();pump();other.unmaximize();other.resize(100,100);other.move(900,800);pump()
        subprocess.run(['xdotool','mousemove','--window',str(xid),'30','20','click','1'],check=True)
        pump(.5)
        from ai_bar.app import x11_pointer_inside_window
        print('inside',x11_pointer_inside_window(xid),'mapped',socket.get_mapped(),flush=True)
        print('focus',hex(d.get_input_focus().focus.id),'target',hex(xid),flush=True)
        subprocess.run(['xdotool','key','a'],check=True);pump(.5)
        title=w.get_full_property(d.intern_atom('_NET_WM_NAME'),0).value
        print('keyboard title',title,flush=True)
        assert title==b'a', title
        monitor.stop()
    finally:
        os.kill(cp,15);os.waitpid(cp,0);os.kill(wp,15);os.waitpid(wp,0)
        shutil.rmtree(profile_path,ignore_errors=True)
    sys.exit(0)
wm=subprocess.Popen(['openbox'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
profile=tempfile.TemporaryDirectory()
chrome=subprocess.Popen(['google-chrome','--no-sandbox','--no-first-run','--disable-background-networking','--user-data-dir='+profile.name,'--app=data:text/html,<body onkeydown="document.title=event.key"><input autofocus>'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
screen=Wnck.Screen.get_default()
try:
    for _ in range(100):
        pump(.1);screen.force_update()
        wins=[w for w in screen.get_windows() if 'chrome' in (w.get_class_group_name() or '').lower()]
        if wins: break
    assert wins
    xid=wins[0].get_xid();print('client',hex(xid),flush=True)
    host=Gtk.Window();socket=Gtk.Socket();host.add(socket);host.show_all();pump()
    while not release_x11_window(xid):pump(.1)
    socket.add_id(xid);pump()
    d=xlib_display.Display();w=d.create_resource_object('window',xid)
    print('embedded',w.get_attributes().map_state,hex(w.query_tree().parent.id),flush=True)
    os.environ['CHECK_STAGE']=json.dumps([xid,chrome.pid,wm.pid,profile.name])
    state=SimpleNamespace(_stop_screen_recording=lambda **kw:None,
        embedded_window_xids={'window:test':xid},xapp_tray_host=None,tray_host=None)
    with patch('ai_bar.app.os.execvp',side_effect=lambda *args:os.execv(sys.executable,[sys.executable,__file__])):
        AiBarWindow._reload(state)
finally:
    chrome.terminate();chrome.wait(timeout=10);wm.terminate();wm.wait(timeout=5)
