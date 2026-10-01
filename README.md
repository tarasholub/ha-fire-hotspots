[![SWUbanner](https://raw.githubusercontent.com/vshymanskyy/StandWithUkraine/main/banner-direct-single.svg)](https://stand-with-ukraine.pp.ua/)

<br>

![Логотип Fire Hotspots](./custom_components/fire_hotspots/brand/logo@2x.png#gh-light-mode-only)
![Логотип Fire Hotspots](./custom_components/fire_hotspots/brand/dark_logo@2x.png#gh-dark-mode-only)

<br>

# 🔥 Fire Hotspots для Home Assistant

[![GitHub Release][gh-release-image]][gh-release-url]
[![GitHub Downloads][gh-downloads-image]][gh-downloads-url]
[![hacs][hacs-image]][hacs-url]
[![License][license-image]][license-url]

[**Українська**](./README.md) | [English](./readme.en.md)

> [!NOTE]
> Супутникові осередки пожеж в обраній країні та її регіонах прямо в Home
> Assistant: лічильники, сенсори «пожежа в регіоні», відстань до найближчого
> осередку, події для автоматизацій і (за бажанням) маркери на мапі.
> Дані: [NASA FIRMS][firms] (VIIRS і MODIS). Межі країн і регіонів:
> [geoBoundaries][geoboundaries].

> [!IMPORTANT]
> Незалежний спільнотний проєкт, не пов'язаний з NASA чи geoBoundaries.
> Супутник фіксує теплову аномалію, а не підтверджену пожежу; дані надходять
> із затримкою до кількох годин після прольоту. Не використовуйте інтеграцію
> як єдине джерело для рішень щодо безпеки.

## Можливості

Для кожного обраного регіону (і для «Всієї країни», якщо обрано):

| Сутність | Приклад | Опис |
| --- | --- | --- |
| `sensor` | Осередки: Херсонська область | Кількість осередків за часове вікно |
| `binary_sensor` (safety) | Пожежа: Херсонська область | «Небезпечно», якщо є хоча б один осередок |

Для країни загалом:

| Сутність | Приклад | Опис |
| --- | --- | --- |
| `sensor` (km) | Найближчий осередок | Відстань від дому HA до найближчого осередку в обраних регіонах |
| `sensor` | Останнє виявлення | Час останнього виявлення в обраних регіонах |
| `sensor` (MW) | Сумарна потужність пожеж | Сумарна FRP (fire radiative power) усіх осередків |
| `event` | Нові осередки | Подія `detected` для кожного регіону з новими осередками |
| `geo_location` | Херсонська область, 01.10 07:29 UTC | Маркер на мапі для кожного осередку (вимкнено за замовчуванням) |

![Сторінка пристрою з сенсорами](./media/device-page.png)

Лічильники мають атрибути `frp_sum` (сумарна FRP у МВт) і `latest_acquired`
(час останнього виявлення в регіоні).

Атрибути події `detected`: `region`, `region_name`, `count` і `detections` —
до 50 осередків, від найближчого до дому, кожен з `latitude`, `longitude`,
`acquired`, `distance`, `confidence`, `frp`, `satellite`, `instrument`,
`daynight`, `source`.

## Встановлення

Найшвидше — через [HACS][hacs-url], натиснувши кнопку:

[![Додати репозиторій у HACS][hacs-install-image]][hacs-install-url]

<details>
  <summary>Якщо кнопка не працює, додайте репозиторій вручну</summary>

1. Відкрийте **HACS** → **⋮** → **Custom repositories**.
2. Вставте `https://github.com/tarasholub/ha-fire-hotspots` як URL репозиторію.
3. Оберіть категорію **Integration**.
4. Знайдіть і встановіть **Fire Hotspots**, перезапустіть Home Assistant.

</details>

Або вручну: скопіюйте `custom_components/fire_hotspots` у
`config/custom_components/` і перезапустіть Home Assistant.

## Налаштування

Отримайте безкоштовний [MAP_KEY][map-key] і натисніть кнопку:

[![Додати інтеграцію Fire Hotspots][install-image]][install-url]

<details>
  <summary>Якщо кнопка не працює, додайте інтеграцію вручну</summary>

1. Відкрийте **Settings** → **Devices & services**.
2. Натисніть **Add integration** і знайдіть **Fire Hotspots**.
3. Пройдіть кроки налаштування.

</details>

Введіть MAP_KEY і оберіть країну. Межі країни завантажаться один раз (для
великих країн — до хвилини) і збережуться в `.storage/fire_hotspots/`. Далі
позначте регіони та/або «Вся країна». Для кожної країни — окремий запис
інтеграції.

<img src="./media/options-flow.png" alt="Вибір регіонів" width="500">

Параметри (кнопка **Configure**):

| Параметр | За замовчуванням | Опис |
| --- | --- | --- |
| Регіони | обрані при налаштуванні | Що відстежувати |
| Часове вікно | 24 год | Враховуються виявлення за останні 1–96 годин |
| Мінімальна достовірність | low | low / nominal / high |
| Супутникові джерела | усі чотири | VIIRS S-NPP, NOAA-20, NOAA-21, MODIS |
| Інтервал оновлення | 30 хв | Як часто опитувати FIRMS (10–180 хв) |
| Показувати на мапі | вимкнено | Маркер для кожного осередку; для всієї країни їх можуть бути сотні |

Часове вікно, достовірність, інтервал і мапа доступні також як сутності в
блоці «Налаштування» на сторінці пристрою — зручно міняти без діалогу:

<img src="./media/config-entities.png" alt="Блок налаштувань на пристрої" width="400">

## Сповіщення про нові осередки

Готовий blueprint — сповіщення на телефон про нові осередки з фільтром
відстані від дому:

[![Імпортувати blueprint][blueprint-image]][blueprint-url]

Або власна автоматизація:

```yaml
automation:
  - alias: "Нові осередки пожеж"
    triggers:
      - trigger: state
        entity_id: event.ukraina_new_hotspots  # перевірте свій entity_id
    conditions:
      - condition: template
        value_template: >
          {{ trigger.to_state.attributes.detections[0].distance is not none
             and trigger.to_state.attributes.detections[0].distance < 20 }}
    actions:
      - action: notify.mobile_app_phone
        data:
          title: "🔥 {{ trigger.to_state.attributes.region_name }}"
          message: >
            Нових осередків: {{ trigger.to_state.attributes.count }},
            найближчий за {{ trigger.to_state.attributes.detections[0].distance }} км
```

## Осередки на мапі

Увімкніть «Показувати на мапі» в налаштуваннях — осередки з'являться на
вбудованій мапі Home Assistant і на map-картці:

```yaml
type: map
geo_location_sources:
  - fire_hotspots
auto_fit: true
```

![Осередки на мапі](./media/map.png)

## Як рахуються осередки

- Оновлення кожні 30 хвилин (налаштовується) через FIRMS Area API: один запит
  на кожне джерело для прямокутника, що покриває обрані регіони. Примусове
  оновлення — стандартна дія `homeassistant.update_entity`.
- Кожне виявлення відноситься до регіону за його полігоном; осередки за
  межами країни відкидаються.
- Налаштування за замовчуванням повторюють підрахунок
  [SaveEcoBot][saveecobot]: усі чотири джерела,
  без фільтра достовірності, без склеювання виявлень різних супутників,
  ковзні 24 години. Під час перевірки збіглися 24 з 27 регіонів України,
  решта — з різницею в один осередок.
- «Вся країна» — це сума регіонів, тобто строго в межах країни. Тому вона може
  бути трохи меншою за загальну цифру SaveEcoBot, яка, схоже, враховує й
  осередки за кілька кілометрів від кордону.
- Осередки, що з'явились, поки Home Assistant був вимкнений, після перезапуску
  все одно надійдуть як нові події.
- Окуповані території України (Крим, частини Донецької, Луганської,
  Запорізької, Херсонської областей) рахуються в межах України.

## Підтримувані країни

Усі країни, які знає Home Assistant і для яких geoBoundaries має межі.
Якщо для країни немає меж регіонів, доступна лише «Вся країна».

Росія та Білорусь не підтримуються як держави-агресорки у війні проти України.

## Видалення

1. Відкрийте **Settings** → **Devices & services**.
2. Оберіть **Fire Hotspots**.
3. У меню **⋮** запису країни натисніть **Delete**.
4. Якщо інтеграція більше не потрібна — видаліть її з HACS і перезапустіть
   Home Assistant.

## Розробка

```bash
scripts/setup     # uv sync + pre-commit
scripts/develop   # Home Assistant на http://localhost:8123 з цією інтеграцією
scripts/lint
scripts/test
```

Або відкрийте репозиторій у Dev Container (`.devcontainer.json`).

### Через Docker

```bash
scripts/docker           # HA на http://localhost:8123, логи в терміналі
scripts/docker restart   # після змін у коді
scripts/docker down      # зупинити
```

Інтеграція монтується в контейнер напряму з `custom_components/fire_hotspots`,
конфігурація HA зберігається в `config/` (у git потрапляє лише
`configuration.yaml`). Інша версія HA: `HA_VERSION=2026.10.0 scripts/docker`.

## Атрибуція та ліцензії

- Код: [MIT](LICENSE).
- Дані про пожежі: NASA FIRMS / LANCE. We acknowledge the use of data and/or
  imagery from NASA's Fire Information for Resource Management System (FIRMS),
  part of NASA's Earth Science Data and Information System (ESDIS).
- Межі: geoBoundaries (gbOpen), Runfola et al. (2020), *PLoS ONE* 15(4):
  e0231866. Межі не входять до репозиторію: кожна інсталяція завантажує їх
  із geoBoundaries за ліцензією відповідної країни (CC BY, ODbL тощо).
- Назва «NASA» використовується лише для вказання джерела даних і не означає
  схвалення NASA.

<!-- Badges -->

[gh-release-url]: https://github.com/tarasholub/ha-fire-hotspots/releases/latest
[gh-release-image]: https://img.shields.io/github/v/release/tarasholub/ha-fire-hotspots?style=flat-square
[gh-downloads-url]: https://github.com/tarasholub/ha-fire-hotspots/releases
[gh-downloads-image]: https://img.shields.io/github/downloads/tarasholub/ha-fire-hotspots/total?style=flat-square
[hacs-url]: https://github.com/hacs/integration
[hacs-image]: https://img.shields.io/badge/hacs-custom-orange.svg?style=flat-square
[license-url]: LICENSE
[license-image]: https://img.shields.io/github/license/tarasholub/ha-fire-hotspots?style=flat-square

<!-- References -->

[firms]: https://firms.modaps.eosdis.nasa.gov/
[geoboundaries]: https://www.geoboundaries.org/
[saveecobot]: https://www.saveecobot.com/analytics/fires
[map-key]: https://firms.modaps.eosdis.nasa.gov/api/map_key/
[hacs-install-url]: https://my.home-assistant.io/redirect/hacs_repository/?owner=tarasholub&repository=ha-fire-hotspots&category=integration
[hacs-install-image]: https://my.home-assistant.io/badges/hacs_repository.svg
[install-url]: https://my.home-assistant.io/redirect/config_flow_start/?domain=fire_hotspots
[install-image]: https://my.home-assistant.io/badges/config_flow_start.svg
[blueprint-url]: https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Ftarasholub%2Fha-fire-hotspots%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Ffire_hotspots%2Fnew_hotspots_notify.yaml
[blueprint-image]: https://my.home-assistant.io/badges/blueprint_import.svg
