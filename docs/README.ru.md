> В этом проекте используется код, сгенерированный ИИ.

[English](../README.md) · **Русский**

# Remix Toolkit для Blender

Импорт захватов RTX Remix и экспорт заменённых мешей, материалов и источников света в мод Remix (`mod.usda`) прямо из Blender. Без RTX Remix Toolkit, Omniverse Kit и фоновых сервисов.

[Релизы](https://github.com/hopelessnes5/blender-remix/releases) · [Сравнение возможностей](PARITY.ru.md) · [Проблемы](https://github.com/hopelessnes5/blender-remix/issues) · [Как работает плагин](PLUGIN_PIPELINE.ru.md)

Проект не связан с NVIDIA. RTX Remix, RTX Remix Toolkit и материалы AperturePBR принадлежат NVIDIA.

## Установка

Нужен Blender 4.0+ под Windows (или Wine в Linux). В ZIP релиза уже включён `texconv`.

1. Скачайте [последний релиз](https://github.com/hopelessnes5/blender-remix/releases).
2. В Blender: `Edit > Preferences > Add-ons > Install...` → `rtx_remix_importer.zip`.
3. Включите **Remix Toolkit for Blender**.

При установке из исходников положите `texconv.exe` в `rtx_remix_importer/texconv/` и установите папку `rtx_remix_importer`. Если Blender не видит изменения кода, удалите папки `__pycache__` и перезапустите его.

## Использование

1. Откройте панель **RTX Remix** на боковой панели 3D Viewport (`N`) и импортируйте захват.
2. Укажите файл мода (`mod.usda`) и целевой подслой.
3. Разместите замены и нажмите `Export Selected Meshes/Lights` или `Export Selected Materials`.
4. `Hide Selected Meshes` / `Restore Hidden Meshes` скрывают захваченную геометрию без экспорта.

`Export Settings` управляет целевой геометрией, масштабом, наследованием материала, скрытием оригинала, кодировкой нормалей и каталогами вывода. Все записи идут в активный целевой слой.

## Ограничения

- Скиннинг-геометрия и импорт не-USD ресурсов (FBX/OBJ/glTF) не поддерживаются.
- `Apply Mod File Changes` — экспериментальная функция.
- Меш с несколькими материалами экспортируется отдельными USD-мешами.

## Благодарности

На основе оригинального *Remix Toolkit for Blender* от sambow23; форк сопровождает [hopelessnes5](https://github.com/hopelessnes5/). Порты кода NVIDIA сохраняют заголовки атрибуции Apache-2.0.
