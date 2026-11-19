# Замена меша в RTX Remix Toolkit: по шагам

Документ составлен по исходникам установленного Toolkit в `O:\RTX Remix Toolkit` и эталонным USD-файлам. Это описание того, **что делает Toolkit**, а не нашего аддона. Пути ниже — от `O:\RTX Remix Toolkit`, если не указано иначе.

Остальные рабочие процессы Toolkit (слои, capture, материалы, текстуры, упаковка): [TOOLKIT_WORKFLOWS.ru.md](TOOLKIT_WORKFLOWS.ru.md).

## Источники

| Что | Файл |
|---|---|
| Имена, префиксы, регулярки хэшей | `exts/lightspeed.common/lightspeed/common/constants.py` |
| Слои capture/replacement, правила edit target | `exts/lightspeed.trex.replacement.core.shared/lightspeed/trex/replacement/core/shared/layers.py`, `.../setup.py` |
| Сбор хэшей capture | `exts/lightspeed.trex.capture.core.shared/lightspeed/trex/capture/core/shared/setup.py` |
| Авторинг замены (reference, `ref_`, material overrides) | `exts/lightspeed.trex.asset_replacements.core.shared/lightspeed/trex/asset_replacements/core/shared/setup.py` |
| Управление материалами | `exts/lightspeed.trex.material.core.shared/lightspeed/trex/material/core/shared/setup.py`, `exts/omni.flux.utils.common/omni/flux/utils/common/materials.py` |
| Импорт ассета | `exts/lightspeed.trex.app.resources/data/validation_schema/model_ingestion.json` |
| Упаковка capture_baker, `preserveOriginalDrawCall` | `exts/lightspeed.event.copy_ref_to_override/lightspeed/event/copy_ref_to_override/core.py` |
| Эталон USD | `exts/lightspeed.trex.app.resources/data/tests/usd/project_example/replacements.usda`, `.../deps/captures/capture.usda`, `.../deps/captures/meshes/mesh_*.usda` |
| Рабочий мод для сверки | `F:\Call_of_Duty_2_Carentan\rtx-remix\mods\gameReadyAssets` |

---

## Шаг 1. Пространство имён capture

Toolkit работает не с именами объектов, а с 16-символьными хэшами в именах примов (`constants.py:53-60`, `113-149`):

- `/RootNode/meshes/mesh_<16HEX>` — прототип меша (геометрия приходит по reference из `captures/meshes/mesh_<HASH>.usd`);
- `/RootNode/instances/inst_<16HEX>_N` — инстанс, ссылается на прототип;
- `/RootNode/Looks/mat_<16HEX>` — материал;
- `/RootNode/lights/light_<16HEX>` — свет.

Связка mesh → material хранится в relationship `material:binding` прямо на prime прототипа (`constants.py:29`, `capture/core/shared/setup.py:238-251`). Все хэши собираются в словарь: для каждого `mesh_*` берётся его `material:binding`, а также обратная группа «материал → все меши, которые его используют» (`capture/core/shared/setup.py:223-255`). Поэтому правка материала считается правкой всех мешей с этим материалом (`async_get_replaced_hashes`, `:258-295`).

---

## Шаг 2. Слои проекта

- Capture-слой — только для чтения; содержит примы и `material:binding`.
- Replacement-слой (`mod.usda`) — записывает только изменения; вместе с подслоями образует стек мода.
- Edit target разрешён только на mod-слой и его подслои (`replacement/core/shared/layers.py:84-97`); capture и root исключены из добавления/блокировки (`layers.py:63-82`, `99-118`).

При импорте replacement-слоя (`replacement/core/shared/setup.py:74-140`):
1. capture-слой уже должен быть загружен (`:84-89`);
2. старый replacement-слой удаляется (`:91-93`);
3. новый слой вставляется в стек (`:94-119`);
4. `timeCodesPerSecond` копируется из capture — без этого удаление reference не работает, OM-42663 (`:121-124`);
5. в `customLayerData` пишется имя игры (`:126-138`).

---

## Шаг 3. Импорт внешнего ассета

Пайплайн задан JSON-схемой `model_ingestion.json`:

1. `AssetImporter` — импорт файла, выход `.usd` (`:18-39`).
2. `ClearUnassignedMaterial` — убрать неиспользуемые материалы (`:41-48`).
3. `DefaultMaterial` — гарантировать материал на каждом меше (`:49-55`).
4. `MaterialShaders` — привязать шейдеры AperturePBR; `AperturePBR_Translucent` для `translucent|glass|trans`, `AperturePBR_Opacity` для остальных (`:56-62`).
5. `ValueMapping` — переписать `inputs:emissive_intensity = 10000` в `1.0` (`:63-70`).
6. `ConvertToOctahedral` и `ConvertToDDS` — нормали и текстуры (`:71-95`).
7. `RelativeAssetPaths` / `RelativeReferences` — все пути становятся relative (`:96-109`).
8. `WrapRootPrims` → `XForms` (`:110-116`).
9. `ApplyUnitScale` с `scale_target = 1.0` (`:117-123`).
10. `WrapRootPrims` → `ReferenceTarget` (`:124-130`).

Поэтому эталонный ассет имеет структуру:

```
#usda 1.0
( defaultPrim = "ReferenceTarget" )
def Xform "ReferenceTarget" {
    rel material:binding = </Looks/mat_<HASH>>
    token visibility = "inherited"
    def Xform "XForms" {
        def Mesh "mesh" {
            ...
            token visibility = "inherited"
        }
    }
}
```

См. `project_example/deps/captures/meshes/mesh_BAC90CAA733B0859.usda:1-32`. Так же устроены ассеты рабочего мода `gameReadyAssets/Level_Specific/Carentan/Carentan_Building_017.usd`: `defaultPrim = /ReferenceTarget`, меш под `XForms/World/...`, `visibility = inherited`.

---

## Шаг 4. Что означает «заменить mesh»

Замена — это не подмена файла и не новый `def mesh_<HASH>`, а два действия в мод-слое:

1. **Заглушить исходный reference** capture-прима: записать `references = None` в мод-слое.
2. **Добавить дочерний `ref_<uuid4hex>`** с reference на новый ассет.

Эталон (`project_example/replacements.usda:126-148`):

```usda
over "mesh_BAC90CAA733B0859" (
    references = None
)
{
    def Xform "ref_c89e0497f4ff4dc4a7b70b79c85692da" (
        prepend references = @./sources/cube.usda@
    )
    {
        custom bool IsRemixRef = 1
        double3 xformOp:translate = (0, 0, 0)
        double3 xformOp:rotateXYZ = (0, 0, 0)
        double3 xformOp:scale = (1, 1, 1)
        uniform token[] xformOpOrder = [...]
    }
}
```

В рабочем моде та же структура: `gameReadyAssets/mod.usda:20-36` и `Building.usda:10-30`. Разница лишь в том, что внутри `ref_` записан `over "XForms" { xformOp:rotateXYZ = (90,0,0); xformOp:scale = (0.01,...) }` — преобразование единиц импортированного ассета (`Building.usda:24-29`).

---

## Шаг 5. Последовательность записи в коде

### 5.1 Найти reference, который нужно заменить

`AssetReplacementsValidators.get_prim_references()` возвращает prim, который **ввёл** reference, и пару `(ref, слой)` (`asset_replacements setup.py:149-157`, `:166-177`). Это важно: reference может быть в capture-слое, а править его нужно в mod-слое.

### 5.2 Сделать путь relative к edit target

`switch_ref_abs_to_rel_path()` (`setup.py:803-809`) считает путь от `stage.GetEditTarget().GetLayer()`, а не от introducing layer. Для `RemoveReference` есть отдельный `__anchor_reference_asset_path_to_layer()` (`setup.py:1035-1053`).

### 5.3 Создать `over` и заглушить reference

`remove_reference()` (`setup.py:1363-1416`):

1. если reference пришёл из другого слоя, путь переякорится и создаётся prim spec со `specifier = Sdf.SpecifierOver` (`:1371-1379`);
2. `SetExplicitReferencesCommand(..., to_set=[])` записывает пустой список — в USD это печатается как `references = None` (`:1402-1408`);
3. если introducing layer ≠ edit target, функция **возвращается сразу** (`:1410-1416`): дочерние примы принадлежат слою, который ввёл reference, их удаление стёрло бы только что написанную заглушку;
4. иначе удаляются дочерние примы, кроме `mesh_*`/`inst_*`/`light_*` (`:1418-1426`), а пустой родительский spec убирается (`:1428-1432`).

### 5.4 Выбрать primPath нового ассета

`get_reference_prim_path_from_asset_path()` (`setup.py:819-839`): если у ассета есть `defaultPrim`, используется специальный тег `<Default Prim>`; при добавлении reference он превращается в пустой `Sdf.Path()` (`:888-890`). Это соответствует `primPath` по умолчанию в эталоне.

### 5.5 Создать `ref_` holder

`__create_child_ref_prim()` (`setup.py:1460-1487`):

- если prim уже помечен `IsRemixRef`, берётся его родитель (`:1462-1466`);
- имя `ref_<uuid4hex>`, следующий свободный path в стейдже (`:1467-1469`);
- создаётся `Xform` командой `CreatePrimCommand` (`:1470-1476`);
- записывается `custom bool IsRemixRef = 1` (`:1479-1485`).

Комментарий в `add_new_reference()` (`:882-883`) объясняет зачем отдельный child: USD не позволяет добавить один и тот же reference дважды, поэтому каждый новый reference уходит на нового ребёнка.

### 5.6 Добавить reference

`add_new_reference()` (`setup.py:871-897`): путь нормализуется и делается relative, `Sdf.Reference(assetPath, primPath)` добавляется командой `AddReference` на `ref_`-холдер.

Для скелетных мешей после этого выполняется remap joint indices и `repair_skinned_replacement_scale()` (`:901-976`, `skeleton.py:295`).

### 5.7 Перенести material:binding

 `_copy_material_overrides()` (`setup.py:637-712`) копирует relationship-оверрайды из старой подиерархии в новую:
- читаются только цели, записанные в самом слое (`explicitItems` или `prependedItems + appendedItems`), а не собранное в стейдже значение (`:682-691`);
- цели внутри source-поддиректории переименовываются на dest-префикс (`:693-696`);
- внешние цели (например `/RootNode/Looks/mat_*`) копируются как есть (`:650-651`);
- рекурсия по подслоям (`:708-712`).

Поиск материалов по приму — `get_materials_from_prim()` (`material/core/shared/setup.py:43`) и `get_materials_from_prim_paths()` (`omni.flux.utils.common/materials.py:22-61`).

---

## Шаг 6. Замена целиком и откат

- `replace_reference()` (`setup.py:988-1033`) — одна undo-группа: `remove_reference()` → выбор primPath → `add_new_reference()`.
- `remove_prim_overrides()` (`setup.py:458-494`) — удаляет prim spec и связанные material-примы рекурсивно по всем подслоям.
- `remove_prim_reference_overrides()` (`setup.py:496-524`) — другая крайность: чистит только reference list edits, сохраняя атрибуты и relationship.
- `delete_prim()` (`setup.py:1434-1458`) — удаляет примы и убирает пустые override-спеки родителей.
- `replace_reference_with_data_model()` (`setup.py:159-193`) — публичная точка входа для UI, работает через edit target.

---

## Шаг 7. Видимость, материал и draw call

1. В capture-слое `mesh_<HASH>` записывает `visibility = invisible` (`project_example/deps/captures/capture.usda:54`, `:62`, `:70`). Это прячет **оригинальный draw call**, но не удаляет `material:binding`.
2. В ассете `visibility = inherited` записывается явно и на корне, и на меше (capture mesh asset `:14`, `:30`). Поэтому замена видна, хотя родитель invisible.
3. Новый `ref_` — ребёнок `mesh_<HASH>`; `material:binding` родителя наследуется через USD-композицию. Отсюда следует: **заменённый меш не должен записывать свой `material:binding` и свой `/Looks`**, если материал не меняется.
4. При упаковке `copy_ref_to_override` решает судьбу оригинального draw call через `preserveOriginalDrawCall`: `0` — reference заглушён и рисовать нечего, `1` — исходный draw call сохраняется вместе с добавленными ref-примами (`copy_ref_to_override/core.py:314-388`, `constants.py:39`).

---

## Шаг 8. Координаты

Toolkit не «запекает» трансформацию инстанса в точки ассета. Ассет — отдельный USD, который попадает под `ref_` внутри прототипа; инстанс `inst_<HASH>_N` применяет свою матрицу по правилам USD reference. Поэтому точки ассета находятся в **локальном пространстве прототипа**.

В рабочем моде это видно напрямую: точки `gameReadyAssets/Level_Specific/Carentan/Carentan_Building_017.usd` заданы в координатах ассета, а дополнительный масштаб/поворот записывается как `over "XForms"` внутри `ref_` (`Building.usda:24-29`). Если записать в ассет уже мировые координаты, движок применит матрицу инстанса второй раз и меш окажется не на месте.

---

## Шаг 9. Итоговая ментальная модель

```
capture.usda (только для чтения)
  /RootNode/meshes/mesh_<HASH>   material:binding = /RootNode/Looks/mat_<HASH>
                                 visibility = invisible
  /RootNode/instances/inst_<HASH>_0  references = mesh_<HASH>

mod.usda (replacement layer)
  over mesh_<HASH> (references = None)      # заглушка исходной геометрии
    def Xform ref_<uuid> (references = @assets/ingested/x.usd@)
      IsRemixRef = 1
      over XForms (опционально: поворот и масштаб)

assets/ingested/x.usd
  defaultPrim = ReferenceTarget
  ReferenceTarget/XForms/.../Mesh
    visibility = inherited
    материал НЕ записывается, если наследуем от capture
    точки в локальных координатах прототипа
```

## Что здесь не описано

- Игровой runtime: как именно движок Remix читает `preserveOriginalDrawCall` и `IsRemixRef` — в исходниках Toolkit этого нет.
- C++-часть ingest/export (`omni.flux.*` validator plugins) доступна только по JSON-схеме и результатам; их внутренний код не читался.
- `apply_handler.py` есть в исходном репозитории `F:/repos/toolkit-remix-1.6.1-0`, но отсутствует в установленном `O:\RTX Remix Toolkit`; Apply через ComfyUI здесь не разбирался.
