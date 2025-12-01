def classify_mood(valence, energy):#Inspired by valence arousal model(https://neurodivergentinsights.com/arousal-valence-model/?srsltid=AfmBOopto2l_HBXOVnFFNnS5mgYbvikDPvGVwIcVdsBXJrRS8F99IPMQ)
    if valence is None or energy is None:
        return "Neutral"

    # --- ROMANTIC ---
    if 0.05 < valence < 0.25 and 0.03 < energy < 0.22:
        return "Romantic"

    # --- HIGH ENERGY ---
    if valence > 0.4 and energy > 0.3:
        return "Euphoric"

    # --- HIGH VALENCE, LOW ENERGY ---
    if valence > 0.3 and energy < 0.1:
        return "Warm"

    # --- MID POSITIVE VALENCE, MID-HIGH ENERGY ---
    if valence > 0.2 and energy > 0.3:
        return "Hype"

    # --- DARK / INTENSE ---
    if valence < -0.2 and energy > 0.3:
        return "Dark"

    # --- SAD / REMINISCING (slow sad songs) ---
    if valence < -0.2 and energy <= 0.3:
        return "Sad"

    # --- MELANCHOLIC---
    if valence < -0.4 and energy < 0.2:
        return "Melancholy"

    # --- CALM / PEACEFUL ---
    if valence > 0.1 and energy < 0.2:
        return "Calm"

    # --- MOODY / INTROSPECTIVE ---
    if -0.35 < valence < 0.15 and 0.05 < energy < 0.45:
        return "Moody"

    # --- DEFAULT ---
    return "Ambient"