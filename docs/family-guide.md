photos-tool — setup guide
==========================

photos-tool copies the photos and videos you pick in the Photos app to the family
Windows PC, as normal picture (JPEG) and video (MP4) files any PC can open, so you
can free up space on your Mac. You set it up once (about 5 minutes); after
that it's two clicks.

You will need:
  - The share address:  smb://192.168.68.70/shared-from-mac
  - The username and password for the share (ask whoever set up the PC).


ONE-TIME SETUP
--------------

1. Install
   Open photos-tool.dmg and drag "photos-tool" onto the Applications folder.

2. Open it the first time
   Double-click photos-tool in Applications. macOS will say it can't open it,
   because the app isn't from the App Store. That's expected. Click Done, then:
     System Settings > Privacy & Security > scroll down > "Open Anyway"
   Confirm with your password or Touch ID. A camera icon (📷) appears at the
   top-right of your screen, in the menu bar.

3. Give it access to your photo library
     System Settings > Privacy & Security > Full Disk Access
   Turn on photos-tool. Then quit it (click 📷 > Quit) and open it again
   from Applications.

4. Connect to the PC
   Click 📷 > "Set up connection…", type the share address above, and click OK.
   It then asks how photos should be saved on the PC. Click "Viewable" (the
   highlighted button): photos become JPEG pictures and videos become MP4 videos
   that open on any PC. If you edited something in Photos, you get the edited
   version. Live Photo movement is not kept. ("Original files" keeps the exact
   iPhone files instead, but the PC needs extra Microsoft add-ons to show them.)
   You can change this later by choosing "Set up connection…" again.
   A macOS window then asks for the username and password: enter them and tick
   "Remember this password in my keychain". Your photos go into their own folder
   on the PC, named after your Mac, so they never mix with anyone else's.

5. (Recommended) Start it automatically
     System Settings > General > Login Items > "+" > photos-tool

6. If you use iCloud Photos
   In Photos > Settings > iCloud, choose "Download Originals to this Mac" and
   let it finish (it needs free space). Otherwise some photos may be skipped.


EVERYDAY USE
------------

Back up photos
   In Photos, select the photos you want (Cmd-A selects everything in a view).
   Click 📷 > "Send Selected Photos".
   The first time, macOS asks if photos-tool may control Photos: click Allow.
   While it works, the icon shows a ticking clock (📷… 0:42). Videos are converted
   as they go, so large batches can take a long time; leave it running. If it's interrupted, just send again:
   it picks up where it left off and never copies a photo twice.

Free up space (optional, only when you're sure)
   After a backup, click 📷 > "Clean up last backup…".
   It first shows you some of the copied photos on the PC so you can check them.
   If you confirm, it moves THAT batch's photos from your Mac to Photos' Recently
   Deleted album. The first time, macOS asks for Photos access: choose Allow
   (or "Full Access" if offered).
   Nothing is erased immediately: Recently Deleted keeps them for about 30 days.
   It only ever removes photos it has confirmed are safely on the PC.
   The JPEG or MP4 on the PC becomes your only copy once Recently Deleted empties:
   the original iPhone file and any Live Photo movement are gone.

Something wrong?
   Click 📷 > "Run Diagnostics" and send a screenshot of the result.


WHAT THE ICON MEANS
-------------------
   📷        Idle
   📷… 1:23  Working (with elapsed time)
   📷✓       Last backup finished OK
   📷⚠️      Last backup needs attention: click 📷 to read the message
   📷✕       photos-tool couldn't run: try Run Diagnostics

Common messages
   "Nothing selected"           Select photos in Photos first, then send.
   "Can't send yet"             Usually the PC is off or asleep, or step 3 was
                                skipped. Wake the PC, then Run Diagnostics.
   "Some photos were skipped"   Usually iCloud photos not downloaded (step 6).
   "Not fully backed up"        The PC copy couldn't be confirmed. Check the PC
                                is on, then send again. Nothing is deleted in
                                this state.
   "A conversion failed"        A photo or video couldn't be converted, so it
                                wasn't backed up. Send again; if it repeats,
                                Run Diagnostics.
