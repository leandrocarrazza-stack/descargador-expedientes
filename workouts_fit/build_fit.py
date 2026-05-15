"""
Genera archivos .FIT de entrenamiento para iGPSPORT iGS300 / Strava.
Targets por FC absoluta (lpm) basados en FCmax estimada = 174 (220-46).
Ajustar tras test real.
"""
from fit_tool.fit_file_builder import FitFileBuilder
from fit_tool.profile.messages.file_id_message import FileIdMessage
from fit_tool.profile.messages.workout_message import WorkoutMessage
from fit_tool.profile.messages.workout_step_message import WorkoutStepMessage
from fit_tool.profile.profile_type import (
    FileType, Sport, Intensity, WorkoutStepDuration, WorkoutStepTarget,
)
import time

# FCmax estimada = 174 lpm. FTHR estimada = 157 lpm (~90% FCmax).
HR_OFFSET = 100  # FIT custom HR target: BPM + 100 indica valor absoluto en lpm

def hr(bpm):
    return bpm + HR_OFFSET

def step(name, duration_s, lo, hi, intensity=Intensity.ACTIVE, notes=None):
    s = WorkoutStepMessage()
    s.workout_step_name = name
    s.intensity = intensity
    if duration_s is None:  # lap button
        s.duration_type = WorkoutStepDuration.OPEN
    else:
        s.duration_type = WorkoutStepDuration.TIME
        s.duration_time = duration_s
    s.target_type = WorkoutStepTarget.HEART_RATE
    s.target_value = 0
    s.custom_target_value_low = hr(lo)
    s.custom_target_value_high = hr(hi)
    if notes:
        s.notes = notes
    return s

def rest(name, duration_s, lo=95, hi=125):
    s = WorkoutStepMessage()
    s.workout_step_name = name
    s.intensity = Intensity.REST
    s.duration_type = WorkoutStepDuration.TIME
    s.duration_time = duration_s
    s.target_type = WorkoutStepTarget.HEART_RATE
    s.target_value = 0
    s.custom_target_value_low = hr(lo)
    s.custom_target_value_high = hr(hi)
    return s

def build(filename, wkt_name, steps):
    builder = FitFileBuilder(auto_define=True)
    fid = FileIdMessage()
    fid.type = FileType.WORKOUT
    fid.manufacturer = 255
    fid.product = 0
    fid.time_created = round(time.time() * 1000)
    fid.serial_number = 0x12345678
    builder.add(fid)

    wkt = WorkoutMessage()
    wkt.workout_name = wkt_name
    wkt.sport = Sport.CYCLING
    wkt.num_valid_steps = len(steps)
    builder.add(wkt)

    for i, st in enumerate(steps):
        st.message_index = i
        builder.add(st)

    fit_file = builder.build()
    fit_file.to_file(filename)
    print(f"OK -> {filename}")

# Zonas (lpm) base FCmax=174 / FTHR=157
Z1_LO, Z1_HI = 95, 118
Z2_LO, Z2_HI = 119, 144
Z3_LO, Z3_HI = 145, 158
SS_LO, SS_HI = 138, 144   # Sweet Spot 88-92% FTHR
Z4_LO, Z4_HI = 149, 165
Z5_LO, Z5_HI = 166, 174

# 1) Cadencia Z1-Z2 - 6x3' alta cadencia 100-110 rpm
w1 = [
    step("Calentamiento", 15*60, Z1_LO, Z2_LO, Intensity.WARMUP),
]
for i in range(6):
    w1.append(step(f"Cadencia {i+1}/6 (100-110rpm)", 3*60, Z2_LO, Z2_HI))
    w1.append(rest(f"Recuperacion {i+1}/6", 3*60, Z1_LO, Z2_LO))
w1.append(step("Vuelta a la calma", 15*60, Z1_LO, Z2_LO, Intensity.COOLDOWN))
build("/home/user/descargador-expedientes/workouts_fit/01_cadencia_Z1Z2.fit",
      "Cadencia Z1-Z2", w1)

# 2) SFR 3x8 Z3 - fuerza-resistencia 60 rpm
w2 = [
    step("Calentamiento", 15*60, Z1_LO, Z2_LO, Intensity.WARMUP),
]
for i in range(3):
    w2.append(step(f"SFR {i+1}/3 (60rpm)", 8*60, Z3_LO, Z3_HI))
    w2.append(rest(f"Recuperacion {i+1}/3", 4*60, Z1_LO, Z2_LO))
w2 += [
    step("Rodaje Z2", 20*60, Z2_LO, Z2_HI),
    step("Vuelta a la calma", 10*60, Z1_LO, Z2_LO, Intensity.COOLDOWN),
]
build("/home/user/descargador-expedientes/workouts_fit/02_SFR_3x8_Z3.fit",
      "SFR 3x8 Z3", w2)

# 3) Sweet Spot 2x20
w3 = [
    step("Calentamiento", 20*60, Z1_LO, Z2_LO, Intensity.WARMUP),
    step("Sweet Spot 1/2", 20*60, SS_LO, SS_HI),
    rest("Recuperacion", 10*60, Z1_LO, Z2_LO),
    step("Sweet Spot 2/2", 20*60, SS_LO, SS_HI),
    step("Vuelta a la calma", 10*60, Z1_LO, Z2_LO, Intensity.COOLDOWN),
]
build("/home/user/descargador-expedientes/workouts_fit/03_SweetSpot_2x20.fit",
      "Sweet Spot 2x20", w3)

# 4) Long Ride Z2 - 3:30
w4 = [
    step("Calentamiento", 15*60, Z1_LO, Z2_LO, Intensity.WARMUP),
    step("Rodaje Z2 (nutricion 60-90g CHO/h)", 3*3600, Z2_LO, Z2_HI),
    step("Vuelta a la calma", 15*60, Z1_LO, Z2_LO, Intensity.COOLDOWN),
]
build("/home/user/descargador-expedientes/workouts_fit/04_LongRide_Z2_330.fit",
      "Long Ride Z2 3h30", w4)

# 5) Test FTHR 20min
w5 = [
    step("Calentamiento", 15*60, Z1_LO, Z2_LO, Intensity.WARMUP),
    step("Progresivo", 5*60, Z2_LO, Z3_LO),
    rest("Z1 previo", 5*60, Z1_LO, Z2_LO),
    step("TEST 20 min - MAXIMO sostenible", 20*60, Z3_LO, Z5_HI),
    step("Vuelta a la calma", 10*60, Z1_LO, Z2_LO, Intensity.COOLDOWN),
]
build("/home/user/descargador-expedientes/workouts_fit/05_Test_FTHR_20min.fit",
      "Test FTHR 20min", w5)

print("\nListo. 5 archivos .FIT generados.")
