# Windows SMB setup

Run these once on the Windows 10/11 PC that will store the family archive.

1. Create or pick a local account for the share, for example `photos`, with a real password. Blank passwords cannot authenticate over SMB.
2. Create the destination folder, for example `C:\FamilyPhotos`.
3. Set the network profile to Private: Settings -> Network & Internet -> Wi-Fi/Ethernet properties -> Network profile type -> Private.
4. Enable sharing: Control Panel -> Network and Sharing Center -> Advanced sharing settings -> Private -> turn on Network discovery and File and printer sharing.
5. Share the folder: right-click `C:\FamilyPhotos` -> Properties -> Sharing -> Advanced Sharing -> Share this folder. In Permissions, add the `photos` user with Change and Read. In the Security tab, make sure the same user has Modify.
6. Keep guest access off. Windows 11 24H2 disables guest shares and requires SMB signing by default; use the authenticated account.
7. Find the PC address with `ipconfig` and note the IPv4 address, for example `192.168.1.50`. A router DHCP reservation is recommended.
8. If the Mac cannot connect, allow File and Printer Sharing for Private networks in Windows Defender Firewall.
9. For viewing originals on Windows, install HEIF Image Extensions and HEVC Video Extensions from Microsoft Store, or install VLC. Otherwise enable `photos-tool` JPEG/MP4 compatibility copies.

Verification from the Mac:

1. Finder -> Go -> Connect to Server.
2. Enter `smb://192.168.1.50/FamilyPhotos`.
3. Log in as the share user and check "Remember this password in my keychain".
4. Create and delete a small test file in the share.

Troubleshooting:

- Cannot see PC: network profile is probably Public, discovery is off, or firewall is blocking sharing.
- Authentication fails: the account has a blank password or macOS is falling back to guest.
- Signature errors: use the authenticated account; do not re-enable guest access.
- Mounts but cannot write: NTFS Security or share Permissions are missing Modify/Change.
- Works then drops: reserve the PC IP address and use `photos-tool` auto-mount.
