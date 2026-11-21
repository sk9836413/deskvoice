---
id: KB-1001
title: Outlook stuck on "Loading profile"
category: Messaging
keywords: outlook, loading profile, stuck, not opening, hang, email, mail
---
## Symptoms
Outlook opens but stays on the "Loading profile" or "Processing" screen and never shows the inbox.

## Steps
1. Close Outlook completely. Open Task Manager and end any "Outlook" process that is still running.
2. Press Windows + R, type `outlook.exe /safe` and press Enter to start Outlook in safe mode.
3. If Outlook opens in safe mode, go to File > Options > Add-ins, choose "COM Add-ins" > Go, and untick all add-ins. Restart Outlook normally.
4. If it still hangs, restart the computer and make sure you are connected to the internet or VPN.
5. As a last step, open Control Panel > Mail > Show Profiles, add a new profile with your work email and set it as the default.

## Raise a ticket if
Outlook does not open even in safe mode, or a new profile also gets stuck.
