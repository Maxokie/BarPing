## BarPing

Small software that allows you to have one or multiple little indicators in your taskbar, that shows if one or multiple of your network devices are online.

You just give it a name and enter its FQDN / IP address, and it'll appear in the taskbar. Green = Online / Dark = Offline

- **you will need the following Python packages**: `pystray`, `Pillow`

you can install the packages with:

```bash
pip install -r requirements.txt
```

**to run it, either execute the .py with Python, or on Windows, you can use the .bat script.**

i didn't really bother checking if BarPing works on any Linux distro or DE since i mainly use it on my Windows laptop, but i'd be happy to check any pull requests ^^

## But how do I use it

- **Add instance**: Click "Add instance" and enter a name and FQDN / IP address.
- **Edit or remove**: Select an item in the list and use "Edit selected" or "Remove selected" to do what it says.
- **Tray icons**: Each instance gets its own tray icon that turns green when online and dark when offline.
