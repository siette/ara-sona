# Ara sona

Una pàgina web petita que mostra **què sona ara al teu compte de Spotify**
(des de qualsevol dispositiu): portada, títol, artista i barra de progrés.
Pensada per a un homelab: es pot posar com a quadre al Homepage o com a enllaç al Homer.

- Només Python (cap llibreria externa), una sola imatge oficial `python:3.13-alpine`.
- No cal construir cap imatge: es munta `app.py` des d'una carpeta teva.
- Fa servir l'API de Spotify (`/v1/me/player`). Només lectura.

## Què necessites

- Un servidor amb **podman** (quadlets) i **nginx** (o qualsevol proxy).
- Un compte de Spotify **Premium** (Spotify ho exigeix per a les aplicacions en mode desenvolupament).
- Un navegador, a l'ordinador des d'on et connectes per SSH.

## Pas 1. Crea l'aplicació a Spotify (al navegador)

1. Entra a <https://developer.spotify.com/dashboard> amb el teu compte.
2. **Create app**.
3. Omple:
   - **App name**: `Ara sona` (el que vulguis)
   - **App description**: el que vulguis
   - **Redirect URI**: `http://127.0.0.1:8888/callback` (exactament així) i **Add**
   - **API/SDKs**: marca **Web API**
4. Accepta les condicions i **Save**.
5. A **Settings** de l'app, copia el **Client ID** i, amb **View client secret**,
   el **Client secret**. No els comparteixis amb ningú ni els pugis a git.

## Pas 2. Prepara les carpetes (per SSH, al servidor)

```bash
mkdir -p ~/quadlets/ara_sona/config
cd ~/quadlets/ara_sona/config
# copia aquí app.py, ara-sona.env.example i ara-sona.container
cp ara-sona.env.example ara-sona.env
chmod 600 ara-sona.env
vim ara-sona.env
```

Posa el Client ID i el Client secret. La línia `DATA_DIR=/config` ha de ser-hi
(és on es guardarà el token).

## Pas 3. Autoritza el teu compte (una sola vegada)

Al servidor:

```bash
podman run --rm -it \
  -v ~/quadlets/ara_sona/config:/config:Z \
  --env-file ~/quadlets/ara_sona/config/ara-sona.env \
  docker.io/library/python:3.13-alpine \
  python /config/app.py auth
```

Et mostrarà una **adreça llarga** que comença per `https://accounts.spotify.com/...`.

1. Copia-la i **obre-la al navegador** (al teu ordinador).
2. Inicia sessió a Spotify si cal i prem **Agree**.
3. El navegador et portarà a una pàgina que **dóna error** (`127.0.0.1:8888 ...`).
   És normal! **Copia l'adreça sencera de la barra del navegador**
   (`http://127.0.0.1:8888/callback?code=...`).
4. Torna a la terminal SSH, **enganxa-la** i prem Intro.

Veuràs que ha guardat el fitxer `refresh_token` a `~/quadlets/ara_sona/config/`.
Aquest fitxer és la teva clau d'accés: **no el pugis mai a git**.

## Pas 4. Arrenca el servei (quadlet)

```bash
mkdir -p ~/.config/containers/systemd
cp ara-sona.container ~/.config/containers/systemd/
vim ~/.config/containers/systemd/ara-sona.container   # revisa la xarxa
systemctl --user daemon-reload
systemctl --user start ara-sona
systemctl --user status ara-sona
```

Si fas servir Fedora/SELinux, el `:Z` del volum ja hi és. Per provar-ho
(amb música sonant a algun dispositiu):

```bash
podman exec ara-sona python -c "import urllib.request as u;print(u.urlopen('http://localhost:8080/api/now').read().decode())"
```

Si no sona res, veuràs `{"state": "idle"}`: és correcte.

## Pas 5. Publica-ho amb nginx

Dins del `server { ... }` del teu domini, **abans** de `location / {`:

```nginx
location = /ara-sona { return 301 /ara-sona/; }
location /ara-sona/ {
    set $upstream_ara ara-sona;
    rewrite ^/ara-sona/(.*)$ /$1 break;
    proxy_pass http://$upstream_ara:8080;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

Cal que nginx tingui un `resolver` (el DNS de la xarxa de podman) definit; si no,
el `set $upstream_ara` no resoldrà el nom `ara-sona`.

Després, dins del contenidor de nginx: `nginx -t && nginx -s reload`.
(El contenidor de nginx i `ara-sona` han d'estar a la mateixa xarxa de podman.)

Ja pots obrir `https://el-teu-domini/ara-sona/`.

## Pas 6. Quadre al Homepage (opcional)

A `services.yaml`:

```yaml
- Ara sona:
    - Spotify:
        icon: spotify
        description: Què sona ara al meu compte
        widget:
          type: iframe
          name: Ara sona
          src: https://el-teu-domini/ara-sona/
          classes: h-72
          allowScrolling: no
          allowFullscreen: false
```

## Pas 7. Enllaç des del Homer (opcional)

Homer no té quadre amb iframe: hi pots posar un **enllaç** que obri la pàgina.

```yaml
  - name: "Què sona?"
    icon: "fas fa-music"
    subtitle: "Ara sona"
    tag: "spotify"
    url: "https://el-teu-domini/ara-sona/"
    target: "_blank"
```

## Notes

- La pàgina consulta Spotify com a màxim cada 5 s (memòria cau) i respecta l'avís 429 de Spotify.
- La pàgina té tema fosc. Per canviar els colors, mira les variables CSS d'`app.py`
  (`--fons`, `--text`, `--fluix`, `--barra`).
- **Seguretat**: no publiquis mai `ara-sona.env` ni `refresh_token`. Qui els tingui
  pot veure què escoltes. Si es filtren, a Spotify fes *Rotate client secret*
  al dashboard i torna a fer el pas 3.
- Qualsevol persona amb accés a la URL veu què sones: no l'exposis a Internet.
- **El `refresh_token` caduca als 180 dies** (des que vas autoritzar, i renovar-ho no ho allarga). Quan passi, la pàgina es posarà en error: torna a fer el pas 3.

## Crèdits

Fet amb l'ajuda de Claude (Anthropic).

