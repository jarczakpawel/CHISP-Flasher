from chisp_flasher import api

project = api.make_project(
    chip='CH32X035',
    transport_kind='usb',
    usb_device='1a86:55e0',
)

print(api.detect(project))
