# Klipper Quick Flash

## Project Goals
* single file (either .py or .pyz)
* use only python builtin libraries, or those already required by klipper
* should be usable by folks with only cursory linux knowledge


## KQL Configuration
KQF's configration lives in ~/.kqf/kqf.cfg, and will be generated on first start if it does not exist.
You can edit you KQF config in your default editor with `kqf configedit`.


### KQF Configration
KQF's own configuration lives in the 'KQF' section


Example:
```
[KQF]
klipper_repo_path: autodetect
klipper_config_path: autodetect
config_flavors_path: ~/.kqf/flavors
firmware_storage: ~/.kqf/firmware
```

| Option                | Required | Type   | Description                                                                                                                             | Default         |
|-----------------------|----------|--------|-----------------------------------------------------------------------------------------------------------------------------------------|-----------------|
| klipper_repo_path     | Yes      | String | Where to find the klipper source, setting this to `autodetect` will cause KQF to try to locate klipper in common locations              | `autodetect`    |
| klipper_config_path   | No       | String | Where to find the klipper printer.cfg, `autodetect` behaves similar to the above. If empty/missing, config will not be used to find MCU | `autodetect`    |
| config_flavors_path   | Yes      | String | Where to store configuration flavors                                                                                                    | ~/.kqf/flavors  |
| firmware_storage_path | Yes      | String | Where to store compiled firmware                                                                                                        | ~/.kqf/firmware |

### Configuring the primary MCU
The configuration for your `primary` MCU lives in the `mcu` section.

Minimal Example:
```
[mcu]
flavor: octopus_pro_v1_2
# mcu_type and mcu_chip will be automatically read from the flavor
# communication_type and communication_id will be extracted from printer.cnf
# bootloader and flash_method will be guessed from mcu_, communication_, and bootloader settings
```

Maximal Example:
```
[mcu]
flavor: spelling_everyting_out
mcu_type: stm32
mcu_chip: stm32f446xx
communication_type: usb
communication_id: a1b2c3d4e5f6
bootloader: katapult
flash_method: katapult
```

### Configuring Additional MCUs.
Additional MCUs are configured with their own sections, named `[mcu NAME_HERE]` (similar to the way they are in klipper)

The names should match those in your printer.cnf

Example:
```
[mcu ebb32]
flavor: ebb32_v1
bootloader: katapult
```
