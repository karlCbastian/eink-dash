# E-ink dashboard
Waveshare 7.5" V2, 800x480, 1-bit. Raspberry Pi 3A+, systemd-timer var 10:e minut.
Pi:n gör `git pull` före varje körning — pushad main hamnar på väggen inom 1 min.

- Testa alltid med `python dash.py --preview`. Genererar PNG, kräver ingen panel.
- `render()` ritar i mode "1". Aldrig "L" + konvertering, det ger dithrad text.
- `push()` måste alltid anropa `epd.sleep()` i finally.
- Källor: SMHI SNOW1gv1, ICS-feeds via ICS_*-variabler i .env, skolmaten.se RSS.
- Varje källa failar tyst i `collect()`. Behåll det.
- .env finns bara på Pi:n och ska aldrig committas.

## Att göra
- Hash-jämförelse i push() så panelen bara ritas om vid ändring
- Utvärdera partiell uppdatering (kolla om epd7in5_V2.py har display_Partial)