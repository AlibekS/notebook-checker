"""Этап 6: зарядка (AC Online), USB-порты (переподключение флешки), Wi-Fi и Bluetooth."""

from __future__ import annotations

import ctypes
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk

from ..common import IS_WINDOWS, PowerShellError, as_list, run_powershell_json
from .base import Step

# Определяет физический порт (LocationPath) каждого подключённого USB-накопителя,
# чтобы отличать новый порт от повторного подключения в тот же самый.
_PS_USB_PORTS = r"""
$ports = @(Get-PnpDevice -PresentOnly -Class DiskDrive | Where-Object { $_.InstanceId -like 'USBSTOR*' } | ForEach-Object {
    $parent = (Get-PnpDeviceProperty -InstanceId $_.InstanceId -KeyName DEVPKEY_Device_Parent).Data
    if ($parent) {
        $lp = (Get-PnpDeviceProperty -InstanceId $parent -KeyName DEVPKEY_Device_LocationPaths).Data
        if ($lp) { "$($lp[0])" }
    }
})
@{ ports = $ports } | ConvertTo-Json -Compress
"""

_PS_WIRELESS = r"""
$wifi = @(Get-NetAdapter -Physical | Where-Object {
        "$($_.PhysicalMediaType)" -match '802\.11|Wireless' -or $_.InterfaceDescription -match 'Wi-?Fi|Wireless|802\.11|WLAN'
    } | ForEach-Object { @{ name = "$($_.InterfaceDescription)"; status = "$($_.Status)" } })
$bt = @(Get-PnpDevice -Class Bluetooth -PresentOnly | Where-Object { $_.InstanceId -notlike 'BTH*' } |
    ForEach-Object { @{ name = "$($_.FriendlyName)"; status = "$($_.Status)" } })
@{ wifi = $wifi; bluetooth = $bt } | ConvertTo-Json -Depth 4 -Compress
"""

_WIFI_STATUS = {"Up": ("включён, подключён к сети", "ok"), "Disconnected": ("включён, сеть не подключена", "ok"),
                "Disabled": ("ОТКЛЮЧЁН в системе", "warn"), "Not Present": ("не обнаружен", "bad")}


def removable_drives() -> set[str]:
    """Буквы съёмных дисков (DRIVE_REMOVABLE) — быстрый вызов WinAPI без PowerShell."""
    if not IS_WINDOWS:
        return set()
    kernel32 = ctypes.windll.kernel32
    mask = kernel32.GetLogicalDrives()
    result = set()
    for i in range(26):
        if mask & (1 << i):
            letter = f"{chr(65 + i)}:\\"
            if kernel32.GetDriveTypeW(ctypes.c_wchar_p(letter)) == 2:
                result.add(letter)
    return result


def ac_online() -> bool | None:
    """True — питание от сети, False — от батареи, None — определить нельзя (нет батареи)."""
    try:
        import psutil
        batt = psutil.sensors_battery()
        if batt is None:
            return None
        return bool(batt.power_plugged) if batt.power_plugged is not None else None
    except Exception:
        return None


class PortsStep(Step):
    step_id = "ports"
    title = "Порты и питание"
    hint = ("1) Отключите и снова подключите зарядку. 2) По очереди переставьте флешку в каждый USB-порт "
            "(программа продолжит работать, пока флешка извлечена). 3) Проверьте статус Wi-Fi и Bluetooth.")

    def build(self, body):
        self.ac_state: bool | None = None
        self.ac_unplugged = False       # зафиксировано отключение зарядки
        self.ac_replugged = False       # зафиксировано повторное подключение
        self.ac_log: list[str] = []
        self.usb_events = 0             # количество подключений накопителя
        self.usb_ports: set[str] = set()  # уникальные физические порты
        self.wireless: dict | None = None
        self._drives: set[str] | None = None
        self._queue: queue.Queue = queue.Queue()
        self._wireless_busy = False

        # --- Питание -------------------------------------------------------
        ac = ttk.LabelFrame(body, text="Зарядка", padding=12)
        ac.pack(fill="x", pady=(0, 10))
        self.ac_big = tk.Label(ac, text="…", font=(self.app.font_family, 16, "bold"), width=22, fg="#ffffff",
                               bg="#8a94a6", pady=6)
        self.ac_big.pack(side="left")
        self.ac_info = ttk.Label(ac, text="", justify="left")
        self.ac_info.pack(side="left", padx=14)

        # --- USB -----------------------------------------------------------
        usb = ttk.LabelFrame(body, text="USB-порты", padding=12)
        usb.pack(fill="x", pady=(0, 10))
        self.usb_big = tk.Label(usb, text="0", font=(self.app.font_family, 26, "bold"), width=4, fg="#ffffff",
                                bg="#2563eb")
        self.usb_big.pack(side="left")
        self.usb_info = ttk.Label(usb, text="", justify="left")
        self.usb_info.pack(side="left", padx=14)

        # --- Беспроводные модули ------------------------------------------
        wl = ttk.LabelFrame(body, text="Беспроводные модули", padding=12)
        wl.pack(fill="x")
        self.wl_text = tk.Text(wl, height=5, relief="flat", bg=self.app.bg, font=self.app.font_normal, wrap="word")
        self.wl_text.pack(side="left", fill="x", expand=True)
        self.wl_text.tag_configure("ok", foreground="#1f7a45")
        self.wl_text.tag_configure("warn", foreground="#a86700")
        self.wl_text.tag_configure("bad", foreground="#c22f2f")
        self.wl_text.configure(state="disabled")
        ttk.Button(wl, text="Обновить", command=self._query_wireless).pack(side="right", anchor="n")

    def on_show(self):
        self._drives = removable_drives()
        self._update_usb_label()
        if self.wireless is None:
            self._query_wireless()
        self._tick()

    def on_hide(self):
        self.cancel_timers()

    # --- опрос раз в 500 мс -------------------------------------------------
    def _tick(self):
        # Питание
        state = ac_online()
        if state is None:
            self.ac_big.configure(text="Батарея не обнаружена", bg="#7a5cc7")
            self.ac_info.configure(text="Проверить переключение питания невозможно:\nсистема не сообщает о батарее.")
        else:
            if self.ac_state is not None and state != self.ac_state:
                stamp = time.strftime("%H:%M:%S")
                if not state:
                    self.ac_unplugged = True
                    self.ac_log.append(f"{stamp} зарядка отключена")
                else:
                    self.ac_replugged = self.ac_unplugged or self.ac_replugged
                    self.ac_log.append(f"{stamp} зарядка подключена")
            self.ac_state = state
            self.ac_big.configure(text="AC Online: ДА" if state else "AC Online: НЕТ",
                                  bg="#1f9d55" if state else "#d98e04")
            mark = lambda flag: "✔" if flag else "○"  # noqa: E731
            self.ac_info.configure(text=f"{mark(self.ac_unplugged)} отключение зарядки зафиксировано\n"
                                        f"{mark(self.ac_replugged)} повторное подключение зафиксировано")
        # USB: новая буква съёмного диска = событие подключения
        drives = removable_drives()
        if self._drives is not None and drives - self._drives:
            self.usb_events += len(drives - self._drives)
            self._update_usb_label()
            threading.Thread(target=self._resolve_ports, daemon=True, name="usb-ports").start()
        self._drives = drives
        # Результаты фоновых запросов
        try:
            while True:
                kind, data = self._queue.get_nowait()
                if kind == "ports":
                    self.usb_ports |= set(data)
                    self._update_usb_label()
                elif kind == "wireless":
                    self._wireless_busy = False
                    self.wireless = data
                    self._render_wireless()
        except queue.Empty:
            pass
        self.later(500, self._tick)

    # --- USB ------------------------------------------------------------------
    def _resolve_ports(self):
        try:
            time.sleep(1.0)  # даём Windows закончить установку устройства
            data = run_powershell_json(_PS_USB_PORTS, timeout=30)
            self._queue.put(("ports", [p for p in as_list(data.get("ports")) if p]))
        except Exception:
            pass  # не удалось определить порт — остаётся простой счётчик подключений

    def _update_usb_label(self):
        self.usb_big.configure(text=str(len(self.usb_ports) or self.usb_events))
        text = f"Подключений накопителя зафиксировано: {self.usb_events}"
        if self.usb_ports:
            text += f"\nРазных физических портов проверено: {len(self.usb_ports)}"
        text += f"\nСъёмных накопителей сейчас подключено: {len(self._drives or ())}"
        self.usb_info.configure(text=text)

    # --- Wi-Fi / Bluetooth ----------------------------------------------------
    def _query_wireless(self):
        if self._wireless_busy:
            return
        if not IS_WINDOWS:
            self.wireless = {"error": "Проверка доступна только в Windows"}
            self._render_wireless()
            return
        self._wireless_busy = True
        self._set_wl([("Опрос модулей…", "")])

        def work():
            try:
                data = run_powershell_json(_PS_WIRELESS, timeout=40)
            except PowerShellError as exc:
                data = {"error": str(exc)}
            except Exception as exc:
                data = {"error": f"Внутренняя ошибка: {exc}"}
            self._queue.put(("wireless", data))

        threading.Thread(target=work, daemon=True, name="wireless").start()

    def _wireless_rows(self) -> list[tuple[str, str]]:
        data = self.wireless
        if data is None:
            return [("Беспроводные модули: не опрашивались", "")]
        if "error" in data:
            return [("Не удалось опросить модули: " + str(data["error"]), "warn")]
        rows = []
        wifi = as_list(data.get("wifi"))
        if not wifi:
            rows.append(("Wi-Fi: устройство не обнаружено", "bad"))
        for adapter in wifi:
            text, level = _WIFI_STATUS.get(adapter.get("status"), (adapter.get("status") or "?", "warn"))
            rows.append((f"Wi-Fi: {adapter.get('name')} — {text}", level))
        bt = as_list(data.get("bluetooth"))
        if not bt:
            rows.append(("Bluetooth: устройство не обнаружено", "bad"))
        for dev in bt:
            ok = dev.get("status") == "OK"
            rows.append((f"Bluetooth: {dev.get('name')} — {'работает' if ok else 'статус ' + str(dev.get('status'))}",
                         "ok" if ok else "warn"))
        return rows

    def _set_wl(self, rows):
        self.wl_text.configure(state="normal")
        self.wl_text.delete("1.0", "end")
        for text, level in rows:
            self.wl_text.insert("end", text + "\n", (level,) if level else ())
        self.wl_text.configure(state="disabled")

    def _render_wireless(self):
        self._set_wl(self._wireless_rows())

    def collect(self):
        return {"ac_online": self.ac_state, "ac_unplug_detected": self.ac_unplugged,
                "ac_replug_detected": self.ac_replugged, "ac_log": list(self.ac_log),
                "usb_connect_events": self.usb_events, "usb_unique_ports": sorted(self.usb_ports),
                "wireless": self.wireless}

    def summary(self):
        yes = lambda flag: "да" if flag else "нет"  # noqa: E731
        usb = f"подключений: {self.usb_events}"
        if self.usb_ports:
            usb += f", разных портов: {len(self.usb_ports)}"
        rows = [("Зарядка", f"отключение зафиксировано: {yes(self.ac_unplugged)}, "
                            f"подключение: {yes(self.ac_replugged)}"),
                ("USB", usb)]
        rows += [("Беспроводная связь", text) for text, _ in self._wireless_rows()]
        return rows
