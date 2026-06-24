# Integration icon

A hard hat beside a sun, thermometer and warning triangle — signalling heat
stress and worker safety.

| File          | Size      | Purpose                          |
|---------------|-----------|----------------------------------|
| `helmet.png`  | 1024×1024 | source illustration              |
| `icon.png`    | 256×256   | brands repo `icon.png`           |
| `icon@2x.png` | 512×512   | brands repo `icon@2x.png`        |

## Showing the icon in Home Assistant

Custom integrations do **not** load icons from this folder. To make the icon
appear in the HA UI, submit the PNGs to
[home-assistant/brands](https://github.com/home-assistant/brands) under:

```
custom_integrations/heat_stress_guidance/icon.png
custom_integrations/heat_stress_guidance/icon@2x.png
```

## Regenerating the PNGs from the source

```sh
magick helmet.png -trim +repage -resize 240x240 -background none -gravity center -extent 256x256 -strip icon.png
magick helmet.png -trim +repage -resize 480x480 -background none -gravity center -extent 512x512 -strip icon@2x.png
```
