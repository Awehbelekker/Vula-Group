import { useState } from "react";
import {
  View, Text, TextInput, TouchableOpacity,
  StyleSheet, ActivityIndicator, KeyboardAvoidingView,
  Platform, ScrollView, Alert,
} from "react-native";
import { useRouter } from "expo-router";
import { login, saveSession } from "../api/vula";

const C = {
  bg: "#F7F4EE", surface: "#FFFFFF", green: "#2C5545",
  border: "#DDD8CE", text: "#2A2A2A", muted: "#8A8680", red: "#C0392B",
};

export default function LoginScreen() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const submit = async () => {
    const e = email.trim().toLowerCase();
    const p = password.trim();
    if (!e || !p) { setError("Email and password are required."); return; }
    setLoading(true);
    setError("");
    try {
      const session = await login(e, p);
      await saveSession(session);
      router.replace("/");
    } catch (err) {
      setError(err.message || "Login failed. Check your credentials and server connection.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <KeyboardAvoidingView
      style={{ flex: 1, backgroundColor: C.bg }}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
    >
      <ScrollView contentContainerStyle={styles.scroll} keyboardShouldPersistTaps="handled">
        {/* Logo */}
        <View style={styles.logoBlock}>
          <Text style={styles.logo}>Vula</Text>
          <Text style={styles.tagline}>Your business AI</Text>
        </View>

        {/* Card */}
        <View style={styles.card}>
          <Text style={styles.cardTitle}>Sign in</Text>
          <Text style={styles.cardSub}>
            Use the email and temporary password from your welcome message.
          </Text>

          <Text style={styles.label}>Email</Text>
          <TextInput
            style={styles.input}
            value={email}
            onChangeText={(v) => { setEmail(v); setError(""); }}
            placeholder="you@company.co.za"
            placeholderTextColor={C.muted}
            autoCapitalize="none"
            autoCorrect={false}
            keyboardType="email-address"
            textContentType="emailAddress"
          />

          <Text style={styles.label}>Password</Text>
          <TextInput
            style={styles.input}
            value={password}
            onChangeText={(v) => { setPassword(v); setError(""); }}
            placeholder="Temp password from your welcome email"
            placeholderTextColor={C.muted}
            secureTextEntry
            textContentType="password"
          />

          {error ? <Text style={styles.error}>{error}</Text> : null}

          <TouchableOpacity
            onPress={submit}
            style={[styles.btn, loading && styles.btnDisabled]}
            disabled={loading}
          >
            {loading
              ? <ActivityIndicator color="#fff" />
              : <Text style={styles.btnText}>Sign in</Text>
            }
          </TouchableOpacity>
        </View>

        <Text style={styles.footer}>
          Not signed up yet? Contact your Vula representative.
        </Text>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  scroll: { flexGrow: 1, justifyContent: "center", padding: 24 },
  logoBlock: { alignItems: "center", marginBottom: 36 },
  logo: { fontSize: 40, fontWeight: "700", color: C.green, letterSpacing: -1 },
  tagline: { fontSize: 14, color: C.muted, marginTop: 4 },
  card: {
    backgroundColor: C.surface,
    borderRadius: 14,
    padding: 24,
    borderWidth: 1,
    borderColor: C.border,
  },
  cardTitle: { fontSize: 20, fontWeight: "700", color: C.text, marginBottom: 6 },
  cardSub: { fontSize: 13, color: C.muted, marginBottom: 24, lineHeight: 20 },
  label: { fontSize: 11, fontWeight: "700", color: C.muted, textTransform: "uppercase", letterSpacing: 0.5, marginBottom: 6 },
  input: {
    backgroundColor: C.bg,
    borderWidth: 1, borderColor: C.border,
    borderRadius: 10, padding: 14,
    fontSize: 14, color: C.text, marginBottom: 16,
  },
  error: { fontSize: 13, color: C.red, marginBottom: 12 },
  btn: { backgroundColor: C.green, borderRadius: 10, padding: 15, alignItems: "center" },
  btnDisabled: { backgroundColor: C.muted },
  btnText: { color: "#fff", fontSize: 15, fontWeight: "700" },
  footer: { textAlign: "center", fontSize: 12, color: C.muted, marginTop: 24 },
});
