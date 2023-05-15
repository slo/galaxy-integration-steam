## NOTE:
In order to properly merge this with Nebula, i have rebased to their fork. The latest version is at 
https://github.com/ABaumher/galaxy-integration-steam/tree/master

# Steam Integration

GOG Galaxy 2.0 Community integration for Steam.

## Dev env setup
* Download Python 3.7.9 32-bit
* Install it with the defaults
* Create a new virtual env:
  `python.exe -m virtualenv .venv -p "C:\Users\<your username>\AppData\Local\Programs\Python\Python37-32\python.exe" --pip 22.0.4`
* Activate the virtual env:
  `.\.venv\Scripts\activate.ps1`
* Install the dev dependencies:
  `pip install -r requirements/dev.txt`
* Make your edits
* Update the protobufs (See README_UPDATE_PROTOBUF_FILES.md for sources)
  Take notice of the initial diff between the files in `protobuf_files` and `protobuf_files/orig`
  Generating the python files is done via:
  `inv generate-protobuf-messages`
* Build your edits:
  `inv build`
* Test your edits:
  `inv test`
* Install your edits for a local test:
  `inv install`
* Build a release package (zip):
  `inv pack`

This is a fork of the repository from FriendsOfGalaxy, intended to continue development until they resume their work.

**This is unofficial and purely maintained by fans!**

## Installation

*~~The latest release should be available for download via the "Connect" button in Galaxy~~*

### To install a custom build:
* make sure Galaxy is closed
* remove the currently installed plugin directory (if present), usually<br>
`%localappdata%\GOG.com\Galaxy\plugins\installed\steam_ca27391f-2675-49b1-92c0-896d43afa4f8`
* create a new folder under a name of your choice (the name doesn't matter) at the following path:<br>
`%localappdata%\GOG.com\Galaxy\plugins\installed\`
* copy the custom build files to the newly created folder

If the latest version available on Github is newer than the version specified in the `manifest.json` file in the custom build, Galaxy will download the newer version and replace the files. To prevent this from happening, you can manually set the version in `manifest.json` to a significantly higher value (e.g. `9.9`).

## Why this fork?

Well, without being too complicated, Steam changed how they do authentication. We used to be able to use one call and magically get a lot of info we needed. But, if we're being honest, it was a little insecure, and it easy. While we were using it for legitimate purposes, not everyone else was, and one of Valve's greatest deterrence to botting or DOS attacks (etc) is making things difficult. The new workflow uses a significant back-and-forth between a Steam Server and ourselves, closely resembling a common web form of authentication called OAuth2. This meant a lot of under-the-hood changes. 

## Credits

### Current Version:
This is a fork of https://github.com/FriendsOfGalaxy/galaxy-integration-steam

The new Authorization flow implementation is heavily influenced by SteamKit. https://github.com/SteamRE/SteamKit<br/>
While we have not utilized their source code, they have implemented the new authentication workflow before we did, and we used their knowledge of how to do so in order to implement it ourselves. If you are doing anything steam related in C#, you should check them out; their project has far more features than our own.

Some work was influenced by ValvePython. https://github.com/ValvePython/steam<br/>
Our projects do the same thing, but use different methods (we use asyncio, they use gevent, for example). Both projects were working on the new Auth Flow simultaneously, with little collaboration between us. That said, their scope is much larger than our own and lets you do a lot more things. If you are looking for a python means of implementing a steam network authentication, you should use their work instead.

### Original Version:

Original Plugin was based on work and research done by others:
* https://github.com/prncc/steam-scraper
* https://github.com/rhaarm/steam-scraper
* https://github.com/mulhod/steam_reviews
* https://github.com/summersb92/aeolipile
* https://github.com/rcpoison/steam-scraper
* https://github.com/chmccc/steam-scraper
