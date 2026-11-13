"""Модуль 1: автоматический паспорт устройства (PowerShell / WMI / CIM).

Каждый раздел собирается отдельным процессом PowerShell в своём потоке:
медленный или сломанный раздел (например, BitLocker без прав администратора)
не блокирует и не роняет остальные.
"""

from __future__ import annotations

import threading
from typing import Callable

from .common import PowerShellError, as_list, fmt_bytes, run_powershell_json

# Вспомогательная функция PowerShell: на части ноутбуков классы root\wmi
# не читаются через CIM, но читаются через устаревший Get-WmiObject.
_PS_WMI_HELPER = r"""
function Get-Wmi2($ns, $cls) {
    $r = @(Get-CimInstance -Namespace $ns -ClassName $cls -ErrorAction SilentlyContinue)
    if ($r.Count -eq 0) { $r = @(Get-WmiObject -Namespace $ns -Class $cls -ErrorAction SilentlyContinue) }
    return ,$r
}
"""

PS_SECTIONS: dict[str, str] = {
    # ---- 1. Идентификация ---------------------------------------------------
    "identity": r"""
$cs   = Get-CimInstance Win32_ComputerSystem
$bios = Get-CimInstance Win32_BIOS
$prod = Get-CimInstance Win32_ComputerSystemProduct
$cpu  = Get-CimInstance Win32_Processor | Select-Object -First 1
$os   = Get-CimInstance Win32_OperatingSystem
$mem  = @(Get-CimInstance Win32_PhysicalMemory | ForEach-Object { [int64]$_.Capacity })
@{
    manufacturer = "$($cs.Manufacturer)".Trim()
    model        = "$($cs.Model)".Trim()
    model_name   = "$($prod.Version)".Trim()
    serial       = "$($bios.SerialNumber)".Trim()
    bios_version = "$($bios.SMBIOSBIOSVersion)".Trim()
    cpu          = "$($cpu.Name)".Trim()
    cpu_cores    = $cpu.NumberOfCores
    cpu_threads  = $cpu.NumberOfLogicalProcessors
    ram_total    = [int64]$cs.TotalPhysicalMemory
    ram_modules  = $mem
    os_caption   = "$($os.Caption)".Trim()
    os_version   = "$($os.Version)"
    hostname     = $env:COMPUTERNAME
} | Out-J
""",
    # ---- 2. Аккумулятор -----------------------------------------------------
    "battery": _PS_WMI_HELPER + r"""
$b      = @(Get-CimInstance Win32_Battery)
$static = Get-Wmi2 'root\wmi' 'BatteryStaticData'
$full   = Get-Wmi2 'root\wmi' 'BatteryFullChargedCapacity'
$cyc    = Get-Wmi2 'root\wmi' 'BatteryCycleCount'
$st     = Get-Wmi2 'root\wmi' 'BatteryStatus'
@{
    present        = ($b.Count -gt 0 -or $static.Count -gt 0)
    name           = "$($b[0].Name)"
    charge_percent = $b[0].EstimatedChargeRemaining
    design         = ($static | Measure-Object -Property DesignedCapacity -Sum).Sum
    full           = ($full   | Measure-Object -Property FullChargedCapacity -Sum).Sum
    cycles         = $cyc[0].CycleCount
    ac_online      = $st[0].PowerOnline
    charging       = $st[0].Charging
} | Out-J
""",
    # ---- 3. Накопители ------------------------------------------------------
    "disks": r"""
$pf = @(Get-CimInstance -Namespace root\wmi -ClassName MSStorageDriver_FailurePredictStatus)
$dd = @(Get-CimInstance Win32_DiskDrive)
$disks = @(Get-PhysicalDisk | Where-Object { "$($_.BusType)" -ne 'USB' } | ForEach-Object {
    $p = $_
    $rc = $p | Get-StorageReliabilityCounter
    $d = $dd | Where-Object { "$($_.Index)" -eq "$($p.DeviceId)" } | Select-Object -First 1
    $predict = $null
    if ($d -and $d.PNPDeviceID) {
        $f = $pf | Where-Object { $_.InstanceName.StartsWith($d.PNPDeviceID, [StringComparison]::OrdinalIgnoreCase) } | Select-Object -First 1
        if ($f) { $predict = [bool]$f.PredictFailure }
    }
    @{
        model           = "$($p.FriendlyName)".Trim()
        size            = [int64]$p.Size
        media_type      = "$($p.MediaType)"
        bus_type        = "$($p.BusType)"
        health          = "$($p.HealthStatus)"
        wmi_status      = "$($d.Status)"
        predict_failure = $predict
        wear            = $rc.Wear
        temperature     = $rc.Temperature
        power_on_hours  = $rc.PowerOnHours
        read_errors     = $rc.ReadErrorsUncorrected
    }
})
@{ disks = $disks } | Out-J
""",
    # ---- 4. Диспетчер устройств --------------------------------------------
    "devices": r"""
$dev = @(Get-CimInstance Win32_PnPEntity -Filter "ConfigManagerErrorCode <> 0" | ForEach-Object {
    @{
        name  = "$($_.Name)"
        class = "$($_.PNPClass)"
        code  = $_.ConfigManagerErrorCode
        id    = "$($_.DeviceID)"
    }
})
@{ devices = $dev } | Out-J
""",
    # ---- 5. Безопасность и ОС ----------------------------------------------
    "security": r"""
$lic = Get-CimInstance SoftwareLicensingProduct -Filter "ApplicationID='55c92734-d682-4d71-983e-d6ec3f16059f' AND PartialProductKey IS NOT NULL" | Select-Object -First 1
$bl = @(); $blSource = 'none'
try {
    $bl = @(Get-BitLockerVolume -ErrorAction Stop | ForEach-Object {
        @{ mount = "$($_.MountPoint)"; volume_status = "$($_.VolumeStatus)"; protection = "$($_.ProtectionStatus)";
           percent = $_.EncryptionPercentage; type = "$($_.VolumeType)" }
    })
    $blSource = 'cmdlet'
} catch {
    try {
        $bl = @(Get-CimInstance -Namespace 'root\cimv2\Security\MicrosoftVolumeEncryption' -ClassName Win32_EncryptableVolume -ErrorAction Stop |
            Where-Object { $_.DriveLetter } | ForEach-Object {
                $prot = switch ($_.ProtectionStatus) { 0 { 'Off' } 1 { 'On' } default { 'Unknown' } }
                @{ mount = "$($_.DriveLetter)"; volume_status = ''; protection = $prot; percent = $null; type = '' }
            })
        $blSource = 'wmi'
    } catch { $blSource = 'error' }
}
$cs = Get-CimInstance Win32_ComputerSystem
$ds = (dsregcmd.exe /status | Out-String)
$tenant = ''
if ($ds -match 'TenantName\s*:\s*(.+)') { $tenant = $Matches[1].Trim() }
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
@{
    license_status   = $lic.LicenseStatus
    license_name     = "$($lic.Name)"
    bitlocker        = $bl
    bitlocker_source = $blSource
    part_of_domain   = [bool]$cs.PartOfDomain
    domain           = "$($cs.Domain)"
    azure_ad_joined  = [bool]($ds -match 'AzureAdJoined\s*:\s*YES')
    workplace_joined = [bool]($ds -match 'WorkplaceJoined\s*:\s*YES')
    tenant           = $tenant
    is_admin         = [bool]$admin
} | Out-J
""",
}

SECTION_TITLES = {
    "identity": "Идентификация",
    "battery": "Аккумулятор",
    "disks": "Накопители",
    "devices": "Диспетчер устройств",
    "security": "Безопасность и ОС",
}

# Расшифровка самых частых кодов ошибок диспетчера устройств
DEVICE_ERROR_CODES = {
    1: "устройство настроено неправильно",
    3: "драйвер повреждён или не хватает памяти",
    10: "запуск устройства невозможен",
    12: "недостаточно свободных ресурсов",
    14: "требуется перезагрузка",
    18: "требуется переустановка драйверов",
    19: "повреждены сведения о конфигурации в реестре",
    21: "устройство удаляется системой",
    22: "устройство отключено",
    24: "устройство отсутствует или работает неправильно",
    28: "драйверы не установлены",
    29: "устройство отключено в BIOS",
    31: "не удалось загрузить драйверы",
    32: "драйвер (служба) отключён",
    37: "не удалось инициализировать драйвер",
    39: "драйвер повреждён или отсутствует",
    40: "нет доступа к оборудованию: ошибка в реестре",
    43: "устройство сообщило о неполадке и было остановлено",
    44: "устройство остановлено приложением или службой",
    45: "устройство не подключено",
    48: "драйвер заблокирован",
    52: "цифровая подпись драйвера не проверена",
}

_JUNK_SERIALS = {"to be filled by o.e.m.", "default string", "system serial number", "none", "n/a",
                 "not specified", "not applicable", "invalid", "chassis serial number", "123456789"}

LICENSE_STATUS = {
    0: "Не активирована",
    1: "Активирована",
    2: "Льготный период (OOB)",
    3: "Льготный период (OOT)",
    4: "Неподлинная копия",
    5: "Режим уведомления (не активирована)",
    6: "Расширенный льготный период",
}


def collect_async(on_section: Callable[[str, dict], None]) -> list[threading.Thread]:
    """Запускает сбор всех разделов в фоновых потоках.

    on_section(имя, данные) вызывается ИЗ ФОНОВОГО потока; при ошибке
    данные имеют вид {"error": "текст"} — исключения наружу не выходят.
    """
    def worker(name: str, script: str) -> None:
        try:
            data = run_powershell_json(script)
            if not isinstance(data, dict):
                data = {"error": "Неожиданный формат ответа"}
        except PowerShellError as exc:
            data = {"error": str(exc)}
        except Exception as exc:  # страховка: поток не должен умирать молча
            data = {"error": f"Внутренняя ошибка: {exc}"}
        on_section(name, data)

    threads = []
    for name, script in PS_SECTIONS.items():
        t = threading.Thread(target=worker, args=(name, script), daemon=True, name=f"ps-{name}")
        t.start()
        threads.append(t)
    return threads


# --- Преобразование сырых данных в строки таблицы ---------------------------
# Строка: (параметр, значение, уровень), уровень: "" | "ok" | "warn" | "bad"
Row = tuple[str, str, str]


def _val(value, suffix: str = "") -> str:
    if value is None or value == "":
        return "—"
    return f"{value}{suffix}"


def battery_health(batt: dict) -> float | None:
    """Остаточное здоровье батареи в %, либо None, если данных нет."""
    try:
        design, full = float(batt.get("design") or 0), float(batt.get("full") or 0)
    except (TypeError, ValueError):
        return None
    if design <= 0 or full <= 0:
        return None
    return round(min(full / design * 100.0, 100.0), 1)


def _rows_identity(d: dict) -> list[Row]:
    modules = [m for m in as_list(d.get("ram_modules")) if m]
    ram = sum(modules) if modules else d.get("ram_total")
    ram_text = fmt_bytes(ram)
    if len(modules) > 1:
        ram_text += f" ({' + '.join(fmt_bytes(m) for m in modules)})"
    model = _val(d.get("model"))
    # У Lenovo «человеческое» название модели лежит в Win32_ComputerSystemProduct.Version
    name = d.get("model_name") or ""
    if name and name.lower() not in model.lower() and "lenovo" in (d.get("manufacturer") or "").lower():
        model = f"{name} ({model})"
    cpu = _val(d.get("cpu"))
    if d.get("cpu_cores"):
        cpu += f" — {d['cpu_cores']} ядер / {d.get('cpu_threads') or '?'} потоков"
    serial = d.get("serial") or ""
    # На платах после замены/перепрошивки в поле серийного номера бывает заглушка
    junk = serial.strip().lower() in _JUNK_SERIALS or not serial.strip("0 ")
    if junk and serial:
        serial_row = ("Серийный номер (BIOS)", f"{serial} — похоже на заглушку, сверьте с наклейкой", "warn")
    else:
        serial_row = ("Серийный номер (BIOS)", _val(serial), "" if serial else "warn")
    return [
        ("Производитель", _val(d.get("manufacturer")), ""),
        ("Модель", model, ""),
        serial_row,
        ("Версия BIOS", _val(d.get("bios_version")), ""),
        ("Процессор", cpu, ""),
        ("Оперативная память", ram_text, ""),
        ("Операционная система", f"{_val(d.get('os_caption'))} ({_val(d.get('os_version'))})", ""),
        ("Имя компьютера", _val(d.get("hostname")), ""),
    ]


def _rows_battery(d: dict) -> list[Row]:
    if not d.get("present"):
        return [("Аккумулятор", "Устройство не обнаружено", "warn")]
    health = battery_health(d)
    if health is None:
        health_row = ("Остаточное здоровье", "нет данных от контроллера батареи", "warn")
    else:
        level = "ok" if health >= 70 else ("warn" if health >= 50 else "bad")
        health_row = ("Остаточное здоровье", f"{health:.0f} %", level)
    ac = d.get("ac_online")
    power = "—" if ac is None else ("от сети" if ac else "от батареи")
    if d.get("charging"):
        power += ", заряжается"
    cycles = d.get("cycles")
    return [
        ("Проектная ёмкость", _val(d.get("design"), " мВт·ч"), ""),
        ("Текущая полная ёмкость", _val(d.get("full"), " мВт·ч"), ""),
        health_row,
        ("Циклов перезарядки", str(cycles) if cycles else "недоступно", ""),
        ("Уровень заряда", _val(d.get("charge_percent"), " %"), ""),
        ("Питание", power, ""),
    ]


def _rows_disks(d: dict) -> list[Row]:
    disks = as_list(d.get("disks"))
    if not disks:
        return [("Накопители", "Устройство не обнаружено", "bad")]
    rows: list[Row] = []
    for i, disk in enumerate(disks, 1):
        prefix = f"Диск {i}: " if len(disks) > 1 else ""
        kind = " / ".join(x for x in (disk.get("media_type"), disk.get("bus_type")) if x and x != "Unspecified")
        rows.append((prefix + "Модель", f"{_val(disk.get('model'))} — {fmt_bytes(disk.get('size'))}"
                     + (f" ({kind})" if kind else ""), ""))
        predict = disk.get("predict_failure")
        health = disk.get("health") or ""
        if predict is True:
            rows.append((prefix + "SMART", "ПРОГНОЗИРУЕТСЯ ОТКАЗ (PredictFailure)", "bad"))
        elif health and health != "Healthy":
            rows.append((prefix + "SMART", f"состояние: {health}", "bad" if health == "Unhealthy" else "warn"))
        elif predict is False or health == "Healthy":
            rows.append((prefix + "SMART", "исправен", "ok"))
        else:
            rows.append((prefix + "SMART", "нет данных", "warn"))
        wear = disk.get("wear")
        if wear is None:
            rows.append((prefix + "Износ", "нет данных", ""))
        else:
            level = "ok" if wear < 70 else ("warn" if wear < 90 else "bad")
            rows.append((prefix + "Износ", f"{wear} % ресурса израсходовано", level))
        temp = disk.get("temperature")
        if temp:
            rows.append((prefix + "Температура", f"{temp} °C", "warn" if temp >= 65 else ""))
        else:
            rows.append((prefix + "Температура", "нет данных", ""))
        if disk.get("power_on_hours"):
            rows.append((prefix + "Наработка", f"{disk['power_on_hours']} ч", ""))
        if disk.get("read_errors"):
            rows.append((prefix + "Неисправимые ошибки чтения", str(disk["read_errors"]), "warn"))
    return rows


def _rows_devices(d: dict) -> list[Row]:
    devices = as_list(d.get("devices"))
    if not devices:
        return [("Проблемные устройства", "не обнаружены", "ok")]
    rows: list[Row] = []
    for dev in devices:
        code = dev.get("code")
        reason = DEVICE_ERROR_CODES.get(code, "ошибка устройства")
        name = dev.get("name") or dev.get("id") or "Неизвестное устройство"
        cls = f" [{dev['class']}]" if dev.get("class") else ""
        # Код 22 — устройство отключено вручную, это не поломка, но проверить стоит
        rows.append((name + cls, f"код {code}: {reason}", "warn"))
    return rows


def _rows_security(d: dict) -> list[Row]:
    rows: list[Row] = []
    status = d.get("license_status")
    if status is None:
        rows.append(("Активация Windows", "лицензия не найдена", "warn"))
    else:
        rows.append(("Активация Windows", LICENSE_STATUS.get(status, f"статус {status}"),
                     "ok" if status == 1 else "warn"))
    volumes = as_list(d.get("bitlocker"))
    if d.get("bitlocker_source") in ("error", "none", None) and not volumes:
        hint = "" if d.get("is_admin") else " (нужны права администратора)"
        rows.append(("BitLocker", "не удалось определить статус" + hint, "warn"))
    elif not volumes:
        rows.append(("BitLocker", "шифруемых томов нет", "ok"))
    for vol in volumes:
        prot, vstat = vol.get("protection") or "", vol.get("volume_status") or ""
        encrypted = prot == "On" or (vstat and vstat != "FullyDecrypted")
        if encrypted:
            pct = f", {vol['percent']} %" if vol.get("percent") is not None else ""
            text = (f"ЗАШИФРОВАН (защита: {prot or '—'}{', ' + vstat if vstat else ''}{pct}) — "
                    "сохраните ключ восстановления или расшифруйте диск до сдачи")
            rows.append((f"BitLocker {vol.get('mount', '')}", text, "warn"))
        else:
            rows.append((f"BitLocker {vol.get('mount', '')}", "не зашифрован", "ok"))
    if d.get("part_of_domain"):
        rows.append(("Домен Active Directory", _val(d.get("domain")), ""))
    else:
        rows.append(("Домен Active Directory", "не в домене", ""))
    if d.get("azure_ad_joined"):
        tenant = f" ({d['tenant']})" if d.get("tenant") else ""
        rows.append(("Azure AD / Entra ID", "присоединён" + tenant, ""))
    elif d.get("workplace_joined"):
        rows.append(("Azure AD / Entra ID", "зарегистрирована рабочая учётная запись", ""))
    else:
        rows.append(("Azure AD / Entra ID", "не присоединён", ""))
    return rows


_ROW_BUILDERS = {
    "identity": _rows_identity,
    "battery": _rows_battery,
    "disks": _rows_disks,
    "devices": _rows_devices,
    "security": _rows_security,
}


def passport_sections(passport: dict) -> list[tuple[str, list[Row]]]:
    """Готовит паспорт к показу: [(заголовок раздела, [строки])]. Используется и в GUI, и в отчёте."""
    result = []
    for name, title in SECTION_TITLES.items():
        data = passport.get(name)
        if data is None:
            rows: list[Row] = [("Сбор данных…", "", "")]
        elif "error" in data:
            rows = [("Не удалось получить данные", str(data["error"]), "warn")]
        else:
            try:
                rows = _ROW_BUILDERS[name](data)
            except Exception as exc:  # неожиданные типы в ответе WMI не должны ронять GUI
                rows = [("Ошибка обработки данных", str(exc), "warn")]
        result.append((title, rows))
    return result


def findings(passport: dict) -> list[tuple[str, str]]:
    """Список замечаний [(уровень, текст)] — всё, что помечено warn/bad."""
    out = []
    for title, rows in passport_sections(passport):
        for label, value, level in rows:
            if level in ("warn", "bad"):
                out.append((level, f"{title}: {label} — {value}"))
    return out
