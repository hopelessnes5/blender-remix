> В этом проекте используется код, сгенерированный искусственным интеллектом.

# Remix Toolkit для Blender

Дополнение для Blender, позволяющее импортировать захваты сцен RTX Remix и экспортировать геометрию, материалы и источники света обратно в мод Remix (`mod.usda`).

Проект не связан с NVIDIA. RTX Remix, RTX Remix Toolkit и материалы AperturePBR принадлежат NVIDIA.

- Автор / сопровождающий: [hopelessnes5](https://github.com/hopelessnes5/)
- Репозиторий: [github.com/hopelessnes5/blender-remix](https://github.com/hopelessnes5/blender-remix)
- Версия: 0.4
- Страница прогресса (HTML): [progress.html](../progress.html) (откройте в браузере)

---

## Сравнение возможностей с официальным RTX Remix Toolkit

Официальный Toolkit — приложение на базе Kit (расширения `lightspeed.trex.*`). Это дополнение повторно реализует те его функции, которые целесообразно использовать непосредственно в Blender. Ниже приведено сравнение по отдельным возможностям.

**Обозначения:** ✅ реализовано · 🟡 частично реализовано / другой подход · ⛔ не реализовано

### Проекты и слои

| Возможность | Компонент официального Toolkit | Статус | Примечания к этому дополнению |
|---|---|:--:|---|
| Создание / открытие проекта мода (`mod.usda`) | `lightspeed.trex.project_wizard.core`, `lightspeed.trex.replacement.core.shared` | ✅ | `Load Remix Project`, `Create New Mod File`; корневой каталог проекта определяется по расположению `mod.usda` |
| Стек подслоёв: добавление существующего, создание нового, выбор целевого слоя для редактирования | `lightspeed.trex.layer_manager.core`, менеджер сцены (stage manager) | 🟡 | Можно создавать и добавлять подслои, а также назначать активный целевой слой; нет дерева с перетаскиванием и изменения порядка слоёв |
| Отключение / блокировка слоёв, изменение порядка перетаскиванием | `lightspeed.trex.layer_manager.core` | 🟡 | Метаданные `omni_layer` `muteness` / `locked` применяются при открытии проекта; переключателей mute/lock и перетаскивания слоёв в интерфейсе нет |
| Автосохранение, проверка проекта, события очистки слоёв | `lightspeed.event.autosave`, `lightspeed.event.validate_project`, `lightspeed.event.layers_cleanup` | ⛔ | |
| Упаковка мода в один файл `.usd` (со сбором зависимостей) | `lightspeed.trex.packaging.core`, `lightspeed.trex.mod_packaging_*` | ⛔ | |
| Настраиваемые каталоги экспорта ресурсов | контекст импорта ресурсов (ingest context) | ✅ | `Mesh Directory` (`assets/ingested`), `Texture Directory` (`rtx-remix/textures`) |

### Импорт захватов сцен

| Возможность | Компонент официального Toolkit | Статус | Примечания к этому дополнению |
|---|---|:--:|---|
| Импорт файла захвата сцены | `lightspeed.trex.capture.core.shared` | ✅ | `Import RTX Remix Capture`; `.usd`, `.usda`, `.usdc` |
| Массовый импорт и браузер захватов с миниатюрами | `lightspeed.trex.home.widget`, мастер проектов | ✅ | Список с флажками, `Batch Import All / Selected`, миниатюры DDS→PNG |
| Преобразование захваченных материалов в материалы для просмотра в Blender | `lightspeed.tool.material.core`, `omni.flux.asset_importer.core` | ✅ | AperturePBR Opaque / Translucent / Model / Normal / SpriteSheet (включённые в комплект `.mdl`); резервный вариант — Principled BSDF |
| Редактирование дерева экземпляров / выделения | `lightspeed.trex.selection_tree.widget` | 🟡 | Для каждого `inst_<hash>_N` создаётся отдельный объект Blender; данные геометрии совместно используются экземплярами одного прототипа; интерфейса дерева прототипов нет |
| Состояние рендеринга / метаданные материалов для отдельных экземпляров | панели геометрии и материалов | 🟡 | Считываются `metadata:*` и устаревший формат `primvars:_remix_metadata`; привязка материала определяется по непосредственному отношению, результату композиции, а также по резервным путям через прототип и дочерние mesh-объекты |
| Преобразование источников света в источники Blender (Sphere / Disk / Cylinder / Rect / Distant) | замена источников света | ✅ | Интенсивность, цвет, радиус, формирование светового конуса |
| Преобразование камер в камеры Blender и выравнивание вида | камера USD-сцены (stage camera) | ✅ | `Select Imported Camera`, `Align View to Imported Camera` |
| Обработка единиц измерения при импорте | ingest `ApplyUnitScale` | 🟡 | Прямой backend считывает `metersPerUnit` из захвата; устаревший backend по-прежнему использует только `Capture Scale` |

### Замена геометрии и создание ресурсов

| Возможность | Компонент официального Toolkit | Статус | Примечания к этому дополнению |
|---|---|:--:|---|
| Замена захваченной геометрии: `over "mesh_<HASH>" ( references = None )` и дочерний `def Xform "ref_<uuid>"` с `prepend references` и `custom bool IsRemixRef = 1` | `lightspeed.trex.asset_replacements.core.shared`, `lightspeed.trex.properties_pane.mesh.widget` | ✅ | `Export Selected Meshes/Lights`; при повторном экспорте используется тот же prim-контейнер, дубликаты и устаревшие переопределения удаляются |
| Скрытие захваченной геометрии без экспорта | `Setup.remove_reference` (ветка межслойной записи) | ✅ | `Hide Selected Meshes` записывает `references = None` в mod-слой на prim, которому принадлежит ссылка (`inst_<HASH>_<N>` для встроенной геометрии); capture-слои никогда не изменяются |
| Структура импортированного ресурса геометрии: `/ReferenceTarget` → `/XForms` → `Mesh`, `MaterialBindingAPI`, `subdivisionScheme`, `extent`, UV-координаты и нормали с интерполяцией face-varying | `lightspeed.trex.asset_pipeline.core` (этап `meta`), IngestCraft | ✅ | Та же структура, которую создаёт официальный конвейер импорта ресурсов, включая `defaultPrim` |
| Нормализация единиц измерения ресурса | ingest `ApplyUnitScale` | 🟡 | Геометрия преобразуется с помощью `Export Scale`, а не `metersPerUnit` |
| Добавление совершенно новой геометрии с экземплярами | импорт ресурса + добавление ссылки | ✅ | Если целевая геометрия не выбрана, создаются `mesh_<HASH>` и `inst_<HASH>_0` |
| Добавление *дополнительной* ссылки на ресурс в prim геометрии (объединение ресурсов) | панель геометрии, «add reference» | ⛔ | Поддерживается только одиночная замена |
| Восстановление / повторное отображение исходной захваченной геометрии | удаление ссылки | ✅ | `Restore Hidden Meshes`; удаляет маску `references = None` из mod-слоя, геометрия не экспортируется |
| Дублирование замены (копирование USD-переопределений) | `copy_replacement_overrides_to_path` | ⛔ | |
| Замена скелетной / скиннинг-геометрии и автоматическое переназначение суставов (`skel:remix_joints`) | `lightspeed.trex.skeleton_replacements.widget`, `.../skeleton.py` | ⛔ | |
| Импорт ресурсов не в формате USD (FBX, OBJ, glTF и т. д.) через `omni.kit.asset_converter` | `omni.flux.asset_importer.core` | ⛔ | На входе и выходе поддерживается только USD |

### Материалы

| Возможность | Компонент официального Toolkit | Статус | Примечания к этому дополнению |
|---|---|:--:|---|
| Создание материалов `AperturePBR_Opacity` / `AperturePBR_Translucent` (слоты текстур и постоянные параметры) | `lightspeed.tool.material.core` | ✅ | Диффузный цвет, металличность, шероховатость, нормали, эмиссия, непрозрачность, зеркальное отражение; пропускание света, IOR, тонкостенность |
| Замена материала захваченной геометрии | `lightspeed.tool.material.core` | 🟡 | Экспортируется новый `mat_<uuid>`, который привязывается с `strongerThanDescendants`; переопределение `over` захваченного `mat_<HASH>` не создаётся |
| Корректный приоритет привязки материала (`bindMaterialAs` как метаданные отношения) | привязка материалов USD | ✅ | Задаётся через `MaterialBindingAPI`, а не через атрибут |
| Группа узлов Aperture в Blender и преобразование Principled ⇄ Aperture | — (создание материалов средствами Blender) | ✅ | `Create Aperture Opaque / Translucent` |
| Копирование / перенос переопределений материалов между prim-объектами | `transfer_property_spec_to_layer`, `transfer_reference_spec_to_layer` | ⛔ | |
| Браузер параметров MDL / пресеты материалов | инструмент материалов | ⛔ | |

### Текстуры

| Возможность | Компонент официального Toolkit | Статус | Примечания к этому дополнению |
|---|---|:--:|---|
| Преобразование текстур в DDS (BC7 / BC5 / BC4) при экспорте | ingest `ConvertToDDS` | ✅ | Обёртка для `texconv`, форматы для отдельных каналов, фоновые задачи |
| Повторное использование существующих текстур по базовому имени материала | — | ✅ | `Reuse Existing Textures` |
| Замена захваченного текстурного ресурса | `lightspeed.trex.texture_replacements.core.shared` | 🟡 | Новые текстуры записываются в экспортируемый материал; пути к текстурам внутри захваченных prim-объектов `mat_<HASH>` не заменяются |
| Октаэдрическое кодирование нормалей | ingest `ConvertToOctahedral` | ⛔ | |
| Сжатие текстур RTXIO | `lightspeed.trex.rtxio.core` | ⛔ | |
| Создание предпросмотра DDS → PNG, исправление повреждённых DDS | — (функции Blender) | ✅ | `Convert DDS to PNG`, `Fix Broken Textures`, очистка кэша текстур |

### Ресурсы, вспомогательные функции и интеграция

| Возможность | Компонент официального Toolkit | Статус | Примечания к этому дополнению |
|---|---|:--:|---|
| Просмотр списка ресурсов, заменяемых модом, и сведений о них | менеджер сцены, `get_replaced_hashes` | ✅ | Панель `Exported Assets`: сканирование, фильтр «нет в сцене», выделение, удаление |
| Повторная загрузка содержимого мода в сцену | перезагрузка через менеджер сцены | 🟡 | `Apply Mod File Changes` (экспериментальная функция) |
| Принудительный повторный экспорт ресурса | — | ✅ | `Invalidate Remix Assets` |
| Отмена / повтор USD-изменений | `lightspeed.trex.commands`, стек отмены Kit | ⛔ | Поддерживается только отмена операций Blender |
| Панели свойств для необработанных атрибутов USD | `lightspeed.trex.properties_pane.*` | ⛔ | |
| Интеграция с ComfyUI | `lightspeed.trex.comfyui.*` | ⛔ | |
| Создание систем частиц (`ParticleSystemAPI`) | редакторы частиц | ⛔ | |
| Служба Stagecraft / совместная работа нескольких пользователей | `lightspeed.trex.service.host`, stagecraft | ⛔ | |
| Пресеты, импорт / экспорт параметров проекта | настройки проекта | ⛔ | |

### Собственные функции дополнения (без аналога в Toolkit)

| Возможность | Где находится |
|---|---|
| Браузер захватов сцен с миниатюрами непосредственно в Blender | Панель `Captures` |
| Создание групп узлов Aperture и резервное использование Principled BSDF | Панель `RTX Remix Materials`, боковая панель Shader Editor |
| Фоновая / параллельная обработка текстур с отображением состояния задач | Панель `Texture Management` |
| Масштаб экспорта, автоматическое применение трансформаций, наследование материала родительского объекта | `Export Settings` |
| Очистка кэша текстур и удаление дублирующихся текстур | Панель `Texture Management` |

---

## Прямой backend NVIDIA (P0)

В импортёре теперь два backend. По умолчанию используется **прямой** (`direct`):
алгоритмы NVIDIA выполняются внутри Blender через `pxr` (OpenUSD), поставляемый
вместе с Blender. При этом не запускаются RTX Remix Toolkit, Omniverse Kit,
StageCraft, MCP-сервер или HTTP/REST-сервис, и не создаются промежуточные файлы.

Старый импортёр сохранён без изменений в
`rtx_remix_importer/legacy_import_core.py` и служит путём отката.

### Архитектура

| Путь | Назначение |
|---|---|
| `rtx_remix_importer/nvidia_core/` | Чистый порт на `pxr`: константы, классификация prim-объектов, проверка захватов и хеши, композиция слоёв, извлечение геометрии. Не импортирует `omni`, `carb` или `bpy`. |
| `rtx_remix_importer/compatibility/kit_adapters.py` | Узкие замены вспомогательных функций Omniverse (`omni.usd`, `omni.flux`), которые использует портированный код. |
| `rtx_remix_importer/blender_bridge/` | Создание mesh-данных Blender, разрешение и назначение материалов, сборка сцены (`scene_builder.import_with_direct_backend`). |
| `rtx_remix_importer/import_core.py` | Фасад с выбором backend и залогированным откатом. Сохраняет сигнатуру и возвращаемое значение старого импортёра. |
| `rtx_remix_importer/legacy_import_core.py` | Прежний импортёр, не изменён; используется для отката. |

### Матрица повторного использования кода NVIDIA

Пути указаны для RTX Remix Toolkit 1.6.1 (`source/extensions/...`, Apache-2.0).

| Источник NVIDIA | Куда портировано | Что именно используется |
|---|---|---|
| `lightspeed.common.lightspeed.common.constants` | `nvidia_core/constants.py` | Значения и регулярные выражения скопированы; удалён импорт `omni.ui`, из-за которого модуль нельзя было использовать вне Kit. |
| `lightspeed.trex.utils.common.prim_utils` | `nvidia_core/prim_utils.py` | Перенесены алгоритмы: классификация, поиск прототипа, поиск ссылок, transferable-спецификации, обнаружение «ghost»-объектов. Неявный контекст `omni.usd` и выделение заменены явными аргументами `stage` / `selection`. |
| `lightspeed.trex.capture.core.shared.setup` | `nvidia_core/capture.py` | Перенесены определение захвата, метаданные слоя, поиск хешей, копирование метаданных сцены и публикация `remix_config`. Менеджер слоёв Kit, миниатюры, листинг через `omni.client` и undo не переносились. |
| Порядок слоёв `lightspeed.layer_manager.core` | `nvidia_core/composition.py` | Повторно реализовано на `pxr`: захват всегда является самым слабым подслоем, mod/replacement-слой становится целевым для редактирования, захват блокируется от записи, а метаданные `omni_layer.muteness` / `omni_layer.locked` рабочего файла применяются при открытии. Полная модель слоёв, интерфейс mute/lock/reorder и события не переносились. |
| `omni.usd.get_composed_references_from_prim`, `omni.usd.get_introducing_layer` | `compatibility/kit_adapters.py` | Повторно реализовано через `Sdf` / `Usd.PrimCompositionQuery`, включая определение слоя-автора и относительные пути к ресурсам. |
| `omni.flux.utils.common.lights.get_light_type` | `compatibility/kit_adapters.py` | Перенесены перечисление типов источников света и исторический псевдоним `DomeLight_1`. |
| Соглашения Toolkit о mesh/prim | `nvidia_core/geometry.py` | Извлечение для Blender написано под ту же структуру `mesh_<HASH>` / `inst_<HASH>_<N>`. N-угольники сохраняются, левосторонняя намотка (`leftHanded`) разворачивается, массивы UV/нормалей на угол остаются согласованными с `faceVertexIndices`. |

**Не перенесено (P1+):** перенос замен ресурсов, конвейеры ingest/packaging,
замена скелета, перенос переопределений материалов/текстур, интеграция ComfyUI,
StageCraft, undo Kit и панели свойств. Преобразование материалов остаётся
существующим кодом дополнения `material_utils.py`, а не портом NVIDIA.

### Переключение backend и откат

Порядок выбора:

1. Переменная окружения `REMIX_IMPORT_BACKEND`.
2. Пользовательское свойство сцены `scene["remix_import_backend"]`.
3. `direct` (по умолчанию).

| Значение | Поведение |
|---|---|
| `direct` (по умолчанию) | Алгоритмы NVIDIA выполняются в процессе; при непредвиденной ошибке импорт логируется и повторяется старым импортёром. |
| `direct-only` | Строгий прямой backend; ошибки пробрасываются, отката нет. |
| `legacy` | Только старый импортёр — мгновенный откат. |

```powershell
# строгий прямой backend
$env:REMIX_IMPORT_BACKEND = "direct-only"

# откат на прежний импортёр
$env:REMIX_IMPORT_BACKEND = "legacy"
```

Чтобы откатиться навсегда, задайте то же значение в свойстве сцены или удалите
пакеты `blender_bridge` / `nvidia_core` / `compatibility` и восстановите
`import_core.py` из `legacy_import_core.py`. Все изменения, кроме переименования
`import_core.py`, являются аддитивными, поэтому достаточно `git revert`
коммита интеграции.

### Семантика импорта

- Слой захвата блокируется в памяти. Целевым слоем для редактирования может быть
  только mod/replacement-слой; `ProjectComposition.add_capture_sublayer()`
  добавляет захват **самым слабым** подслоем, предварительно удаляя предыдущий.
- При значении `Capture Scale = 0.01` (по умолчанию в интерфейсе) прямой backend
  использует собственные `metersPerUnit` и `upAxis` захвата (обычно `0.01` и
  Y-up). Любое другое значение интерфейса считается явным переопределением.
- Один datablock mesh в Blender совместно используется всеми экземплярами
  прототипа. Материалы привязываются на уровне объекта
  (`slot.link = 'OBJECT'`), поэтому экземпляры могут различаться без
  дублирования геометрии.
- Материалы захвата разрешаются через прямое отношение `material:binding`,
  результат композиции, затем через прототип и дочерние mesh-объекты.
  Недействительные цели (например, устаревший путь `/RootNode/Looks/...`)
  отбрасываются.

### Тесты

Тесты находятся в `tests/` и должны запускаться внутри Blender, поскольку пакет
дополнения импортирует `bpy`:

```powershell
& "C:\Blender 4.5 LTS\blender.exe" `
  --background --factory-startup --python tests/run_tests.py
```

Фикстуры в `tests/data/full_project/` скопированы из тестовых данных Toolkit
`lightspeed.layer_manager.core` (Apache-2.0). Набор покрывает адаптеры Kit,
утилиты prim, помощники захвата, композицию/блокировку, геометрию (N-угольники,
левосторонняя намотка, индексы primvar), неизменность захвата, выбор/откат
backend и реальный импорт в Blender файлов `capture.usda` и
`full_project.usda`. Тесты композиции также проверяют, что muteness из
`omni_layer` действительно исключает отключённые подслои, что слои из
`omni_layer.locked` становятся read-only, что сохранение проекта пишет только
mod-слой, а запись в заблокированный захват вызывает ошибку.
`tests/blender/test_ui_operators.py` прогоняет настоящие
UI-операторы (`remix.import_capture`, `remix.scan_capture_folder`) целиком,
включая переключение backend через свойство сцены и путь ошибки `direct-only`.
`tests/unit/test_hide.py` и `TestHideMeshOperators` покрывают скрытие и
восстановление захваченной геометрии: маска пишется на prim экземпляра,
владеющий ссылкой, хеш capture-файла не меняется, каталог `assets/` не
появляется, а скомпонованная геометрия исчезает и возвращается.

**Фактический результат:** 89 тестов проходят и на Blender 4.5.5 LTS
(встроенный OpenUSD 25.02), и на Blender 5.1.2 (встроенный OpenUSD 25.08) под
Windows, код возврата `0`; `register()` / `unregister()` также проверены на обеих
версиях. Свежесобранный `rtx_remix_importer.zip` установлен через
`preferences.addon_install` в чистый профиль на обеих версиях и импортировал
фикстуру (763 вершины, 1 материал, backend `direct`). `git diff --check` и
`python -m compileall` проходят.

**Не проверено:**

- Не было реального игрового захвата с текстурами; использовались только
  фикстуры Toolkit (два файла DDS). Blender 4.0–4.4 и Linux/Wine не запускались.
- В этом окружении группа узлов `Aperture Opaque` не подключалась, поэтому
  преобразование материалов проверялось через существующий резервный
  Principled BSDF.
- Применение `omni_layer` muteness/locked проверено на фикстуре Toolkit. Проекты,
  в которых отключён или заблокирован сам capture либо mod-слой, не покрыты: эти
  слои защищены и намеренно не отключаются, тогда как официальный Toolkit может
  предлагать для них другую политику интерфейса.
- Импорт источников света делегируется существующему `light_utils.py`, который
  по-прежнему предупреждает о возможном неверном повороте для Y-up.
- Пути Windows с не-ASCII символами и Linux/Wine не проверялись.

---

## Системные требования

- Windows (или Wine под Linux)
- Blender 4.0 или новее (проверено в фоновом режиме на 4.5.5 LTS и 5.1.2)
- [texconv из DirectXTex](https://github.com/microsoft/DirectXTex/releases/latest) — требуется только при установке из исходников; в ZIP-архив релиза уже включён

## Установка

### Из релиза

1. Скачайте последний релиз с [github.com/hopelessnes5/blender-remix/releases](https://github.com/hopelessnes5/blender-remix/releases).
2. В Blender откройте `Edit > Preferences > Add-ons > Install...` и выберите `rtx_remix_importer.zip`.
3. Включите дополнение **Remix Toolkit for Blender**.

### Из исходного кода

1. Клонируйте репозиторий.
2. Поместите `texconv.exe` в `rtx_remix_importer/texconv/` (при необходимости создайте папку). Файл `texconv.exe` исключён из Git, поэтому отсутствует в клонированном репозитории.
3. Откройте `Edit > Preferences > Add-ons > Install...` и выберите папку `rtx_remix_importer` (либо скопируйте её в `scripts/addons/`).
4. Включите дополнение **Remix Toolkit for Blender**.

Если Blender не применяет изменения в коде дополнения, удалите кэш байт-кода с помощью скрипта `delete-py-cache-and-open-blender.ps1` в корне репозитория.

---

## Использование

### Импорт захватов сцен

- Откройте панель **RTX Remix** на боковой панели 3D Viewport (клавиша `N`).
- В параметре `Captures > Capture Folder` укажите папку с захватами сцен игры.
- Импортируйте один захват или отметьте несколько и используйте `Batch Import Selected`.

### Проект и подслои

- `RTX Remix Project > Remix Mod File` — укажите существующий `mod.usda` или создайте новый.
- `RTX Remix Project > Sublayers` — выберите целевой подслой или создайте новый.
- Все экспортируемые данные записываются в активный целевой слой.

### Экспорт геометрии и источников света

1. Выберите импортированный объект в `Export Settings > Target Mesh` (захваченную геометрию, хеш которой нужно заменить). Если экспортируется объект, импортированный из захвата, он автоматически используется как целевой.
2. Импортируйте или создайте геометрию / источники света и разместите их в сцене.
3. Выделите их и нажмите `Export Selected Meshes/Lights`.

В целевой слой записывается следующее:

```usda
over "RootNode"
{
    over "meshes"
    {
        over "mesh_<16HEX>" ( references = None )
        {
            def Xform "ref_<32HEX>" ( prepend references = @./assets/ingested/<mesh>.usda@ )
            {
                custom bool IsRemixRef = 1
                ... identity translate / rotateXYZ / scale ...
            }
        }
    }
}
```

Ресурс, на который указывает ссылка, представляет собой самостоятельный USD-ресурс геометрии:

```usda
def Xform "ReferenceTarget" ( kind = "group" )      # defaultPrim
{
    def Xform "XForms" ( kind = "group" )
    {
        def Mesh "<mesh>" ( prepend apiSchemas = ["MaterialBindingAPI"] )
        {
            rel material:binding = </ReferenceTarget/Looks/<mesh>__> (
                bindMaterialAs = "weakerThanDescendants"
            )
            uniform token subdivisionScheme = "none"
        }
    }
    def "Looks" { def Material "<mesh>__" { ... } }
}
```

### Скрытие и восстановление захваченной геометрии (без экспорта)

1. Выделите одну или несколько импортированных захваченных мешей.
2. Нажмите `Hide Selected Meshes` — дополнение запишет только в mod-слой:

```usda
over "RootNode"
{
    over "instances"
    {
        over "inst_<16HEX>_<N>" ( references = None )
        {
        }
    }
}
```

3. Нажмите `Restore Hidden Meshes` с тем же выделением, чтобы снять маску.

Геометрия, материалы и текстуры не экспортируются. Capture-слой открывается
только для чтения и никогда не изменяется; скрытие отклоняется, если у prim уже
есть собственная ссылка на замену. Во встроенных захватах геометрия лежит в
prototype `mesh_<HASH>`, а видимой её делает `prepend references` на
`inst_<HASH>_<N>`, поэтому маска пишется на экземпляр. Путь `mesh_<HASH>` тоже
принимается — для захватов, где сама prototype ссылается на внешнюю геометрию.

### Экспорт материалов

1. Выберите геометрию, материал которой нужно заменить, и откройте Shader Editor.
2. Захваченные материалы импортируются в виде групп узлов Aperture; также поддерживается `Principled BSDF`.
3. Для создания материала с нуля воспользуйтесь `Create Aperture Opaque / Translucent` на боковой панели Shader Editor.
4. Нажмите `Export Selected Materials`.

### Параметры экспорта

| Параметр | По умолчанию | Описание |
|---|---|---|
| `Target Mesh` | – | Захваченная геометрия `mesh_<HASH>`, которую заменит экспортируемый ресурс |
| `Export Scale` | `0.01` | Игровые единицы на одну единицу Blender; `0.01` подходит для большинства игр Remix |
| `Auto Apply Transforms` | вкл. | Применяет вращение / масштаб к экспортируемой геометрии |
| `Hide Target Mesh` | вкл. | Записывает `references = None`, чтобы заменить захваченную геометрию, а не наложить новую поверх неё |
| `Inherit Parent Material` | выкл. | Сохраняет захваченный материал вместо экспорта нового |
| `Reuse Existing Textures` | вкл. | Повторно использует DDS-файлы по базовому имени материала |
| `Mesh Directory` | `assets/ingested` | Каталог для сохранения импортированных ресурсов геометрии |
| `Texture Directory` | `rtx-remix/textures` | Каталог для сохранения экспортируемых DDS-текстур |

### Экспортированные ресурсы

Панель `Exported Assets` показывает все ссылки на геометрию и источники света, созданные в выбранных слоях. Доступны действия выделения / удаления и фильтр `Show Assets Not in Scene`.

---

## Известные проблемы

- Экспорт скиннинг-геометрии пока не поддерживается (нет замены скелета и переназначения суставов).
- Функция `Apply Mod File Changes` экспериментальная и может работать непредсказуемо.
- Замена источников света пока не связана с `/RootNode/lights/light_<HASH>` — источники света, экспортированные без целевой геометрии, помещаются в `/RootNode/remix_assets`.
- Текстуры альбедо с альфа-каналом могут некорректно проходить полный цикл импорта и экспорта.
- Некоторые текстуры `.dds` отображаются фиолетовыми; используйте `Captures > Fix Broken Textures`.
- Группа узлов `Aperture Opaque` реализована не полностью (анимация, иризация, флаги и т. д.).
- Импорт ресурсов не в формате USD (FBX/OBJ/glTF) не поддерживается.
- Для каждого prim-объекта геометрии можно создать только одну ссылку на ресурс; объединение нескольких ресурсов в одну геометрию не поддерживается.

## Устранение неполадок

| Проблема | Решение |
|---|---|
| `NameError` / дополнение не включается после обновления | Очистите кэш байт-кода: `delete-py-cache-and-open-blender.ps1`, затем перезапустите Blender |
| Текстуры не преобразуются в DDS | Отсутствует `texconv.exe` в `rtx_remix_importer/texconv/` |
| Фиолетовые текстуры в окне просмотра | Используйте `Captures > Fix Broken Textures` |
| При экспорте появляется сообщение «No Target Mesh selected» | Выберите захваченную геометрию в `Export Settings > Target Mesh` |

## Благодарности

- Uncle Burrito (Discord-сервер RTX Remix Showcase) — за группу узлов Aperture Opaque.
- На основе оригинального *Remix Toolkit for Blender* от sambow23; сопровождением этого форка занимается [hopelessnes5](https://github.com/hopelessnes5/).

Сообщения о проблемах и запросы на включение изменений (pull requests): [github.com/hopelessnes5/blender-remix/issues](https://github.com/hopelessnes5/blender-remix/issues)
