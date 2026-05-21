import { useState, useEffect } from "react";
import { View, Text, TextInput, TouchableOpacity, StyleSheet, Alert } from "react-native";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { setHost, setApiKey } from "../src/api/vula";

const C = {
  bg: "#F7F4EE", surface: "#FFFFFF", green: "#2C5545",
  border: "#DDD8CE", text: "#2A2A2A", muted: "#8A8680",
};

export default function Settings() {
  const [host, setHostInput] = useState("");
  const [apiKey, setApiKeyInput] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    Promise.all([
      AsyncStorage.getItem("vula_host"),
      AsyncStorage.getItem("vula_api_key"),
    ]).then(([h, k]) => {
      if (h) setHostInput(h);
      if (k) setApiKeyInput(k);
    });
  }, []);

  const save = async () => {
    const trimmedHost = host.trim();
    if (!trimmedHost.startsWith("http")) {
      Alert.alert("Invalid URL", "Host must start with http:// or https://");
      return;
    }
    await setHost(trimmedHost);
    await setApiKey(apiKey.trim());
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  return (
    <View style={styles.container}>
      <Text style={styles.sectionLabel}>Backend host</Text>
      <Text style={styles.hint}>Your desktop or Vula Box IP on the same WiFi.</Text>
      <TextInput
        style={styles.input}
        value={host}
        onChangeText={(v) => { setHostInput(v); setSaved(false); }}
        placeholder="http://192.168.1.100:7438"
        placeholderTextColor={C.muted}
        autoCapitalize="none"
        autoCorrect={false}
        keyboardType="url"
      />

      <Text style={[styles.sectionLabel, { marginTop: 20 }]}>API Key</Text>
      <Text style={styles.hint}>Set if your server has API_KEY configured. Leave blank for local dev.</Text>
      <TextInput
        style={styles.input}
        value={apiKey}
        onChangeText={(v) => { setApiKeyInput(v); setSaved(false); }}
        placeholder="Leave blank for open dev mode"
        placeholderTextColor={C.muted}
        autoCapitalize="none"
        autoCorrect={false}
        secureTextEntry
      />

      <TouchableOpacity onPress={save} style={styles.btn}>
        <Text style={styles.btnText}>{saved ? "Saved ✓" : "Save Settings"}</Text>
      </TouchableOpacity>

      <View style={styles.divider} />
      <Text style={styles.sectionLabel}>Quick host presets</Text>
      {["http://192.168.1.100:7438", "http://192.168.1.101:7438", "http://10.0.0.2:7438"].map((h) => (
        <TouchableOpacity key={h} onPress={() => setHostInput(h)} style={styles.preset}>
          <Text style={styles.presetText}>{h}</Text>
        </TouchableOpacity>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, padding: 24, backgroundColor: C.bg },
  sectionLabel: { fontSize: 12, fontWeight: "700", color: C.text, marginBottom: 4, textTransform: "uppercase", letterSpacing: 0.5 },
  hint: { fontSize: 12, color: C.muted, marginBottom: 12 },
  input: { backgroundColor: C.surface, borderColor: C.border, borderWidth: 1, borderRadius: 10, padding: 14, fontSize: 14, color: C.text, marginBottom: 8 },
  btn: { backgroundColor: C.green, borderRadius: 10, padding: 14, alignItems: "center", marginTop: 4 },
  btnText: { color: "#fff", fontSize: 14, fontWeight: "600" },
  divider: { height: 1, backgroundColor: C.border, marginVertical: 28 },
  preset: { padding: 12, backgroundColor: C.surface, borderColor: C.border, borderWidth: 1, borderRadius: 8, marginBottom: 8 },
  presetText: { fontSize: 13, color: C.text, fontFamily: "monospace" },
});
