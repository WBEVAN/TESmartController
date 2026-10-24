# Windows

Confirm Python and pip before `pip install` or `tesmartctl install`.

## Python and pip

In Command Prompt, both of these must print a version:

```cmd
python --version
python -m pip --version
```

If `python` is not the interpreter you installed, try the Python launcher and use that same command for pip:

```cmd
py -3 --version
py -3 -m pip --version
```

Python 3.10 or newer is required. `python -m pip` (or `py -3 -m pip`) installs into that same Python, which is the one that provides `tesmartctl`. If `python` opens the Microsoft Store instead of printing a version, install Python from [python.org](https://www.python.org/downloads/windows/) with **Add python.exe to PATH** selected, then run the checks again.

Continue with [Setup](Setup), then [Quick start](Quickstart) on the server or [Client install](Client-Install) on another computer.

## Double quotes for pip

Command Prompt keeps single quotes as part of the text. When a pip command in the README or these pages is wrapped in single quotes, type it with double quotes:

```cmd
pip install "tesmartctl[serial]"
pip install "tesmartctl[mdns]"
pip install "tesmartctl[kit]"
pip install -e ".[serial]"
```

## Server and client in separate windows

`tesmartctl quickstart` stays in its window for as long as the server runs. Open the server and a client in separate Command Prompt windows with [`start`](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/start). The first quoted string is the window title. The command follows it:

```cmd
start "TESmart server" tesmartctl quickstart
start "TESmart client" tesmartctl --via auto kit
```

[`/b`](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/start) keeps the command in this console instead of opening a new window. The first quoted string is still the window title, so leave it empty:

```cmd
start /b "" tesmartctl quickstart
start /b "" tesmartctl --via auto kit
```

With `/b`, Ctrl+C is ignored. Use Ctrl+Break to stop the command. Run `tesmartctl install` in each role before those lines. The server has to keep running. Close it and the listener stops.
