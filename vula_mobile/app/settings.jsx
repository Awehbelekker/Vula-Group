import { useState, useEffect } from "react";
import { View, Text, TextInput, TouchableOpacity, StyleSheet, Alert } from "react-native";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { setHost } from "../src/api/vula";

const C = {
  bg: "#F7F4EE", surface: "#FFFFFF", green: "#2C5545",
  border: "#DDD8CE", text: "#2A2A2A", muted: "#8A8680",
};

export default function Settings() {
  const [host, setHostInput] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    AsyncStorage.getItem("vula_host").then((v) => {
      if (v) setHostInput(v);
    });
  }, []);

  const save = async () => {
    const trimmed = host.trim();
    if (!trimmed.startsWith("http")) {
      Alert.alert("Invalid URL", "Host must start with http:// or https://");
      return;
    }
    await setHost(trimmed);
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  return (
    <View style={styles.container}>
      <Text style={styles.label}>Backend host</Text>
      <Text style={styles.hint}>Your desktop or Soul Box IP on the same WiFi network.</Text>
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
      <TouchableOpacity onPress={save} style={styles.btn}>
        <Text style={styles.btnText}>{saved ? "Saved" : "Save"}</Text>
      </TouchableOpacity>

      <View style={styles.divider} />
      <Text style={styles.sectionLabel}>Quick set</Text>
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
  label: { fontSize: 13, fontWeight: "600", color: C.text, marginBottom: 4 },
  hint: { fontSize: 12, color: C.muted, marginBottom: 14 },
  input: { backgroundColor: C.surface, borderColor: C.border, borderWidth: 1, borderRadius: 10, padding: 14, fontSize: 14, color: C.text, marginBottom: 12 },
  btn: { backgroundColor: C.green, borderRadius: 10, padding: 14, alignItems: "center" },
  btnText: { color: "#fff", fontSize: 14, fontWeight: "600" },
  divider: { height: 1, backgroundColor: C.border, marginVertical: 28 },
  sectionLabel: { fontSize: 11, color: C.muted, textTransform: "uppercase", letterSpacing: 1, marginBottom: 12 },
  preset: { padding: 12, backgroundColor: C.surface, borderColor: C.border, borderWidth: 1, borderRadius: 8, marginBottom: 8 },
  presetText: { fontSize: 13, color: C.text, fontFamily: "monospace" },
});
