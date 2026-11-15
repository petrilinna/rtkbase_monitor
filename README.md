# rtkbase_monitor
This repository includes files to be installed on an RTK base station. The programs monitor the status of the base station and send it to Home Assistant -service, which is located on the same local network.


Tämä ohje koskee Python-ohjelmia:

- **`rtkbase_monitor.py`** 
	– lukee Raspberry Pi:n ja GNSS-vastaanottimen tilatietoja paikallisesti ja julkaisee ne MQTT:hen.

- **`rtkbase_ntrip_check.py`** – tarkistaa RTK2go:n NTRIP-mountpointista kerran ajokertaa kohti, että saadaanko RTCM3-muotoista dataa. Ohjelma tekee **enintään yhden TCP-yhteyden ajokertaa kohti**, ei yritä uudelleen eikä muuta RTKBase-asetuksia. Tämän ohjelman käytössä on vaara, että joutuu estolistalle, mutta yksi kerta vuorokaudessa ei vielä pitäisi sitä tehdä. 

Ohjelmat on tehty Raspberry Pi -pohjaiseen RTKBase-asennukseen, jossa on `gpsd`/`gpspipe`, `str2str`, MQTT-välittäjä (esim. Mosquitto) ja Home Assistantin MQTT-integraatio. Esimerkeissä käytetään Linux-käyttäjää `pi`: korvaa se omalla käyttäjätunnuksellasi.

## 1. Kopioi ohjelmat Raspberry Pi:lle

Tallenna molemmat `.py`-tiedostot Raspberry Pi:n käyttäjän kotihakemistoon, esimerkiksi:

```text
/home/pi/rtkbase_monitor.py
/home/pi/rtkbase_ntrip_check.py
```

Voit kopioida ne toiselta tietokoneelta esimerkiksi SCP:llä:

```bash
scp rtkbase_monitor.py rtkbase_ntrip_check.py pi@RASPBERRY_PI_IP:/home/pi/
```

Vaihda `RASPBERRY_PI_IP` ja `pi` oman laitteesi tietoihin. Jos tiedostojen nimissä on latauksesta johtuva `(1)`-pääte, nimeä ne ensin yllä oleviin nimiin.

## 2. Asenna riippuvuudet

Tarvitaan Python 3, `paho-mqtt` ja GNSS-tietojen lukemiseen `gpspipe` (`gpsd-clients`-paketti). Raspberry Pi:n lämpötila luetaan `vcgencmd`-komennolla, jos se on saatavilla.

Debian/Raspberry Pi OS -ympäristössä esimerkiksi:

```bash
sudo apt update
sudo apt install -y python3 python3-paho-mqtt gpsd-clients
```

Tarkista komennot:

```bash
gpspipe -h
vcgencmd measure_temp
```

Jos `gpspipe` ei saa tietoja, varmista että RTKBase käyttää GPSD:tä ja että GPSD on käytettävissä. Koodin GNSS-tilatiedot perustuvat `gpspipe -w -n 20` -komennon SKY-viesteihin.

## 3. Luo MQTT- ja NTRIP-asetustiedosto

**Älä kirjoita salasanoja Python-tiedostoihin.** Molemmat ohjelmat lukevat asetukset käyttäjän kotihakemiston tiedostosta `~/.rtkbase_mqtt`.

```bash
nano ~/.rtkbase_mqtt
```

Lisää esimerkiksi:

```ini
MQTT_HOST=192.168.1.10
MQTT_PORT=1883
MQTT_USER=oma_mqtt_kayttaja
MQTT_PASSWORD=oma_mqtt_salasana
NTRIP_HOST=rtk2go.com
NTRIP_PORT=2101
NTRIP_MOUNT=OMA_TUKIASEMAN_NIMI
```

**Oman tukiaseman nimen vaihtaminen:** korvaa `NTRIP_MOUNT=OMA_TUKIASEMAN_NIMI` täsmälleen sillä **mountpoint-nimellä**, jolla tukiasemasi lähettää dataa RTK2go-palveluun. Nimi löytyy RTKBase:n NTRIP-lähetysasetuksista tai RTK2go:n sourcetablesta. Esimerkiksi jos mountpoint on `MY_BASE_01`, kirjoita:

```ini
NTRIP_MOUNT=MY_BASE_01
```

Kirjoita vain mountpoint-nimi, **ei** `http://`-osoitetta, porttia tai NTRIP-salasanaa. Tämä tarkistusohjelma käyttää mountpointia vastaanottavan yhteyden avaamiseen eikä tarvitse tukiaseman lähetyssalasanaa. Jos oma NTRIP-palvelimesi vaatii vastaanottajalta tunnistautumisen, nykyinen tarkistusohjelma ei tue sitä.

**Tärkeää:** nykyisen `rtkbase_ntrip_check.py`-koodin oletusarvo on `FIN_PORI_LINNA`, jos `NTRIP_MOUNT` puuttuu. Siksi **aseta `NTRIP_MOUNT` aina itse**. Halutessasi voit myös muuttaa Python-tiedoston rivin:

```python
mount = cfg.get('NTRIP_MOUNT', 'FIN_PORI_LINNA')
```

muotoon:

```python
mount = cfg['NTRIP_MOUNT']
```

Tällöin ohjelma ei vahingossa tarkista alkuperäisen tekijän tukiasemaa, jos oma asetus puuttuu.

Suojaa asetustiedosto:

```bash
chmod 600 ~/.rtkbase_mqtt
```

Huomaa, että ohjelmat käyttävät `Path.home()`-polkua: suorita ne **samana Linux-käyttäjänä**, jonka kotihakemistossa `.rtkbase_mqtt` sijaitsee. Älä suorita `sudo python3 ...`, ellei asetustiedostoa ole tarkoituksella luotu root-käyttäjälle.

## 4. Testaa paikallinen monitorointi

```bash
python3 ~/rtkbase_monitor.py
```

Ohjelma tulostaa JSON-muotoisia tilatietoja ja julkaisee ne MQTT-aiheeseen:

```text
rtkbase/state
```

Esimerkkejä kentistä:

- `cpu_temperature`, `cpu_load`, `memory_usage`, `disk_usage`
- `satellites_visible`, `satellites_used`, `hdop`, `pdop`
- `gps_visible`, `gps_used`, `galileo_visible`, `galileo_used` sekä BeiDou- ja GLONASS-tiedot
- `gnss_online` – saatiinko GPSD:ltä SKY-viesti
- `ntrip_running` – löytyykö käynnissä oleva `str2str`-prosessi, jonka komentorivillä on `ntrips://`
- `last_update` – mittausajankohta

**Huomio:** `ntrip_running=True` tarkoittaa vain, että lähetysprosessi on käynnissä. Se **ei todista**, että RTK2go todella vastaanottaa korjausdataa. Tätä varten on erillinen päivittäinen tarkistus.

Jos `gnss_online` on `false`, tarkista `gpspipe -w -n 20` ja RTKBase/GPSD-kokoonpano. Jos `ntrip_running` on `false`, tarkista RTKBase:n käyttämä lähetysohjelma ja yhteysmuoto: nykyinen koodi etsii nimenomaan `str2str`-prosessia ja `ntrips://`-tekstiä.

## 5. Testaa RTK2go-yhteys harkiten

```bash
python3 ~/rtkbase_ntrip_check.py
```

Tämä tekee **yhden todellisen NTRIP-vastaanottoyrityksen** määritettyyn palvelimeen/mountpointiin. Älä toista testiä tiheästi: RTK2go voi rajoittaa toistuvia yhteyksiä.

Tulos julkaistaan aiheeseen:

```text
rtkbase/daily_ntrip
```

Tuloskentät:

- `status`: `ok`, `no_data`, `unreachable`, `rejected`, `invalid_response` tai `configuration_error`
- `bytes_received`: vastaanotettujen tavujen määrä
- `checked_at`: tarkistusajankohta

`ok` tarkoittaa, että vastaanotetusta datasta löytyi kokonainen RTCM3-kehys. **Koodi ei tarkista RTCM:n CRC:tä**, joten tulos ei yksin todista korjausdatan laatua. `rejected` voi johtua esimerkiksi palvelimen vastaustavasta tai käyttörajoituksista, ei välttämättä tukiaseman viasta.

## 6. Ajasta ohjelmat cronilla

Avaa **saman käyttäjän** crontab:

```bash
crontab -e
```

Lisää tiedoston loppuun:

```cron
*/10 * * * * /usr/bin/python3 /home/pi/rtkbase_monitor.py >> /home/pi/rtkbase_monitor.log 2>&1
0 3 * * * /usr/bin/python3 /home/pi/rtkbase_ntrip_check.py >> /home/pi/rtkbase_ntrip_check.log 2>&1
```

**Korvaa `/home/pi/` omalla kotihakemistollasi** (esim. `/home/oma_kayttaja/`). Ensimmäinen ohjelma ajetaan 10 minuutin välein; RTK2go-tarkistus **vain kerran vuorokaudessa klo 03.00** Raspberry Pi:n paikallista aikaa.

Tarkista lokit:

```bash
tail -n 30 ~/rtkbase_monitor.log
tail -n 30 ~/rtkbase_ntrip_check.log
```

Jos käytät virtuaaliympäristöä, vaihda cronin `/usr/bin/python3` virtuaaliympäristön Python-polkuun.

## 7. Home Assistant ja MQTT Discovery

1. Varmista, että MQTT-välittäjä (esim. Mosquitto) toimii ja Raspberry Pi pääsee siihen.
2. Ota Home Assistantissa **MQTT-integraatio** käyttöön ja yhdistä se samaan välittäjään.
3. Aja `rtkbase_monitor.py` kerran. Se julkaisee Home Assistantin MQTT Discovery -määritykset aiheisiin `homeassistant/.../config` (retained).
4. Aja halutessasi `rtkbase_ntrip_check.py` kerran, jotta myös päivittäisen tarkistuksen kolme anturia luodaan.
5. Etsi Home Assistantin laitteista **RTKBase Raspberry Pi** ja lisää haluamasi entiteetit dashboardiin.

Koodissa MQTT-aiheet ovat kiinteästi `rtkbase/state` ja `rtkbase/daily_ntrip`. Laitteen tunniste on `rtkbase_raspberry`. Jos **useampi tukiasema julkaisee samaan MQTT-välittäjään**, jokaiselle on määritettävä oma `PREFIX` **ja** `DEVICE` molempiin Python-tiedostoihin, jotta tilaviestit ja Home Assistant -entiteetit eivät mene päällekkäin. Pelkkä mountpointin vaihtaminen ei riitä erottamaan useita asemia samassa MQTT-järjestelmässä.

Huomaa, että ohjelmien Discovery-määritykset käyttävät `retain=True`-asetusta. Jos muutat tunnisteita, vanhat Home Assistant -entiteetit voivat jäädä näkyviin, kunnes niiden retained Discovery -määritykset poistetaan.

## 8. Tietoturva ja rajoitukset

- **Älä jaa `~/.rtkbase_mqtt`-tiedostoa**: se sisältää MQTT-salasanan.
- Älä jaa RTKBase:n NTRIP-lähetyssalasanoja tai muita salaisia asetuksia.
- Python-koodit eivät sisällä kovakoodattuja MQTT-salasanoja; `rtkbase_ntrip_check.py`:ssä on kuitenkin alkuperäisen tukiaseman mountpoint oletusarvona (ks. kohta 3).
- MQTT-yhteys on koodissa tavallinen TCP-yhteys; **TLS-salausta ei ole määritetty**. Käytä luotettua lähiverkkoa tai lisää TLS-tuki ennen käyttöä epäluotetun verkon yli.
- `rtkbase_monitor.py` ei muuta GNSS-vastaanottimen tai RTKBase:n asetuksia.
- `rtkbase_ntrip_check.py` tekee oikean ulkoisen verkkoyhteyden: pidä sen ajastus harvana.


## 9. Vianmäärityksen pikavinkit

| Ongelma | Tarkista |
|---|---|
| `FileNotFoundError: .rtkbase_mqtt` | Asetustiedosto oikean käyttäjän kotihakemistossa |
| `KeyError: MQTT_USER` tai `MQTT_PASSWORD` | Puuttuva asetus tiedostosta |
| MQTT-yhteys epäonnistuu | `MQTT_HOST`, portti, käyttäjätunnus, salasana ja palomuuri |
| `ModuleNotFoundError: paho` | `python3-paho-mqtt` asennettu käytössä olevalle Pythonille |
| `gnss_online: false` | `gpspipe`/GPSD ja GNSS-vastaanottimen yhteys |
| `ntrip_running: false` | `str2str`-prosessi ja sen käyttämä yhteysmuoto |
| Päivittäinen tarkistus ei anna `ok` | Mountpoint, verkkoyhteys, palvelimen vastaus ja RTCM-lähetys |
| Home Assistant ei näytä laitetta | MQTT-integraatio, Discovery, MQTT-välittäjä ja ohjelman loki |

---
