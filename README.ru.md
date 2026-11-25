<h1 align="center">ExporTo для Autodesk Fusion</h1>
<p align="center"><strong>Отправляйте модели из Fusion прямо в слайсер или CAD-программу.</strong><br>Выберите установленное приложение в контекстном меню. ExporTo сам создаст файл STEP или STL.</p>

<p align="center"><a href="https://github.com/4FusionLab/ExporTo/releases/latest">Скачать ExporTo</a> · <a href="README.md">English</a> · <a href="https://github.com/4FusionLab/ExporTo/issues">Сообщить о проблеме</a></p>

## Установка

1. Скачайте ZIP релиза и распакуйте папку `ExporTo` в постоянное место.
2. Во Fusion откройте **Utilities → Scripts and Add-Ins**. Выберите **+ → Script or add-in from device** и укажите распакованную папку.
3. Нажмите **Run** и включите **Run on Startup**.

## Экспорт из дерева модели

1. Выберите одно или несколько тел, компонентов или вложенных сборок. Можно также выбрать корень модели. Нажмите правой кнопкой по выделению.
2. Для STEP откройте **Export to**. Для сетки в миллиметрах откройте **Export to → STL**.
3. Выберите **File…**, чтобы сохранить файл на компьютер, или выберите приложение, чтобы открыть в нём экспорт.

При экспорте нескольких объектов создаётся один файл. Тела сохраняют положение в сборке.

В меню появляются приложения, доступные на вашем компьютере. Среди распространённых вариантов Bambu Studio, OrcaSlicer, PrusaSlicer и FreeCAD. Если установлен Blender, **Export to → STL → Blender** откроет новую сцену с сеткой. ExporTo использует иконки установленных приложений, когда они доступны.

## Очистка кэша экспорта

Пункт **Export to → Clear cache** показывает общий размер временных STEP и STL. Нажмите его, чтобы удалить эти файлы. Дождитесь завершения импорта в выбранном приложении перед очисткой.

## Совместимость

Сборка ExporTo предназначена для настольного Autodesk Fusion на macOS и Windows.

ExporTo — независимый проект [4FusionLab](https://github.com/4FusionLab). Условия распространения описаны в [PolyForm Noncommercial License 1.0.0](LICENSE).
