Go to `https://discord.com/developers/applications/`.<br>
Click `New Application` in top-right and create name for your discord bot.

Go to Bot<br>
Click `Reset Token` and copy it.<br>
Create `token.txt` in the main folder and paste the copied token there.<br>
Scroll down, set `Message Content Intent` to enabled.<br>
Save changes.

Go to OAuth2<br>
select "Scopes": `applications.commands` and `bot`<br>
select "Bot Permissions":
* easy, unsafe: `Administrator`
* advanced, safer: `View Channels`, `Manage Channels`, `Send Messages`, `Embed Links`, `Attach Files`, `Read Message History`, `Add Reactions`

Copy link from the bottom, paste into browser and continue to Discord to add Bot to your server.