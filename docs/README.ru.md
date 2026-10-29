> В этом проекте используется код, сгенерированный искусственным интеллектом.

# Remix Toolkit для Blender

Дополнение для Blender, позволяющее импортировать захваты сцен RTX Remix и экспортировать геометрию, материалы и источники света обратно в мод Remix (`mod.usda`).

Проект не связан с NVIDIA. RTX Remix, RTX Remix Toolkit и материалы AperturePBR принадлежат NVIDIA.

- Автор / сопровождающий: [hopelessnes5](https://github.com/hopelessnes5/)
- Репозиторий: [github.com/hopelessnes5/blender-remix](https://github.com/hopelessnes5/blender-remix)
- Версия: 0.4

---

## Сравнение возможностей с официальным RTX Remix Toolkit

Официальный Toolkit — приложение на базе Kit (расширения `lightspeed.trex.*`). Это дополнение повторно реализует те его функции, которые целесообразно использовать непосредственно в Blender. Ниже приведено сравнение по отдельным возможностям.

**Обозначения:** ✅ реализовано · 🟡 частично реализовано / другой подход · ⛔ не реализовано

### Проекты и слои

| Возможность | Компонент официального Toolkit | Статус | Примечания к этому дополнению |
|---|---|:--:|---|
| Создание / открытие проекта мода (`mod.usda`) | `lightspeed.trex.project_wizard.core`, `lightspeed.trex.replacement.core.shared` | ✅ | `Load Remix Project`, `Create New Mod File`; корневой каталог проекта определяется по расположению `mod.usda` |
| Стек подслоёв: добавление существующего, создание нового, выбор целевого слоя для редактирования | `lightspeed.trex.layer_manager.core`, менеджер сцены (stage manager) | 🟡 | Можно создавать и добавлять подслои, а также назначать активный целевой слой; нет дерева с перетаскиванием и изменения порядка слоёв |
| Отключение / блокировка слоёв, изменение порядка перетаскиванием | `lightspeed.trex.layer_manager.core` | ⛔ | |
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
| Состояние рендеринга / метаданные материалов для отдельных экземпляров | панели геометрии и материалов | 🟡 | Считываются `metadata:*` и устаревший формат `primvars:_remix_metadata`; привязка материала определяется только по непосредственному отношению `material:binding` |
| Преобразование источников света в источники Blender (Sphere / Disk / Cylinder / Rect / Distant) | замена источников света | ✅ | Интенсивность, цвет, радиус, формирование светового конуса |
| Преобразование камер в камеры Blender и выравнивание вида | камера USD-сцены (stage camera) | ✅ | `Select Imported Camera`, `Align View to Imported Camera` |
| Обработка единиц измерения при импорте | ingest `ApplyUnitScale` | 🟡 | `Capture Scale` (по умолчанию `0.01`); значение `metersPerUnit` не считывается |

### Замена геометрии и создание ресурсов

| Возможность | Компонент официального Toolkit | Статус | Примечания к этому дополнению |
|---|---|:--:|---|
| Замена захваченной геометрии: `over "mesh_<HASH>" ( references = None )` и дочерний `def Xform "ref_<uuid>"` с `prepend references` и `custom bool IsRemixRef = 1` | `lightspeed.trex.asset_replacements.core.shared`, `lightspeed.trex.properties_pane.mesh.widget` | ✅ | `Export Selected Meshes/Lights`; при повторном экспорте используется тот же prim-контейнер, дубликаты и устаревшие переопределения удаляются |
| Структура импортированного ресурса геометрии: `/ReferenceTarget` → `/XForms` → `Mesh`, `MaterialBindingAPI`, `subdivisionScheme`, `extent`, UV-координаты и нормали с интерполяцией face-varying | `lightspeed.trex.asset_pipeline.core` (этап `meta`), IngestCraft | ✅ | Та же структура, которую создаёт официальный конвейер импорта ресурсов, включая `defaultPrim` |
| Нормализация единиц измерения ресурса | ingest `ApplyUnitScale` | 🟡 | Геометрия преобразуется с помощью `Export Scale`, а не `metersPerUnit` |
| Добавление совершенно новой геометрии с экземплярами | импорт ресурса + добавление ссылки | ✅ | Если целевая геометрия не выбрана, создаются `mesh_<HASH>` и `inst_<HASH>_0` |
| Добавление *дополнительной* ссылки на ресурс в prim геометрии (объединение ресурсов) | панель геометрии, «add reference» | ⛔ | Поддерживается только одиночная замена |
| Восстановление / повторное отображение исходной захваченной геометрии | удаление ссылки | ⛔ | Необходимо вручную удалить дочерний prim `ref_` |
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

## Системные требования

- Windows (или Wine под Linux)
- Blender 4.0 или новее (разработка и тестирование проводились на 4.5 LTS)
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
