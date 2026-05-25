import { useState, useEffect, useCallback } from "react";
import {
  View, Text, TextInput, TouchableOpacity, StyleSheet,
  Alert, ScrollView, ActivityIndicator, RefreshControl,
} from "react-native";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { useRouter } from "expo-router";
import { setHost, setApiKey, getSession, clearSession, getTenantStatus } from "../src/api/vula";

const C = {
  bg: "#F7F4EE", surface: "#FFFFFF", green: "#2C5545",
  border: "#DDD8CE", text: "#2A2A2A", muted: "#8A8680",
  red: "#C0392B", amber: "#D97706", blue: "#1D4ED8",
};

const PLAN_LABELS = {
  starter:  { label: "Starter", price: "R1,500/mo", color: C.blue },
  growth:   { label: "Growth",  price: "R3,500/mo", color: C.green },
  business: { label: "Business", price: "R7,500/mo", color: "#7C3AED" },
};

function daysUntil(isoDate) {
  if (!isoDate) return null;
  const ms = new Date(isoDate).getTime() - Date.now();
  return Math.ceil(ms / (1000 * 60 * 60 * 24));
}

function SubscriptionCard({ session }) {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const fetchStatus = useCallback(async () => {
    if (!session?.tenant_id) { setLoading(false); return; }
    setLoading(true);
    setError(null);
    try {
      const data = await getTenantStatus(session.tenant_id);
      setStatus(data);
    } catch (e) {
      setError("Could not load subscription status.");
    } finally {
      setLoading(false);
    }
  }, [session?.tenant_id]);

  useEffect(() => { fetchStatus(); }, [fetchStatus]);

  const plan = PLAN_LABELS[status?.plan ?? session?.plan] ?? { label: session?.plan ?? "—", price: "", color: C.muted };
  const trialEnds = status?.trial_ends ?? session?.trial_ends;
  const days = daysUntil(trialEnds);
  const isPaid = status?.status === "active" && status?.paid;
  const isExpired = days !== null && days < 0;
  const isExpiringSoon = !isPaid && days !== null && days >= 0 && days <= 7;

  let trialBadgeColor = C.green;
  let trialText = trialEnds ? `Trial ends ${trialEnds}` : "Trial";
  if (isPaid) {
    trialText = "Active subscription";
    trialBadgeColor = C.green;
  } else if (isExpired) {
    trialText = "Trial expired";
    trialBadgeColor = C.red;
  } else if (isExpiringSoon) {
    trialText = `Trial ends in ${days} day${days === 1 ? "" : "s"}`;
    trialBadgeColor = C.amber;
  } else if (days !== null) {
    trialText = `${days} days left in trial`;
  }

  return (
    <View style={styles.subCard}>
      <View style={styles.subHeader}>
        <View>
          <Text style={styles.companyName}>{session?.company_name}</Text>
          <Text style={styles.emailText}>{session?.email}</Text>
        </View>
        <View style={[styles.planBadge, { backgroundColor: plan.color + "18", borderColor: plan.color + "40" }]}>
          <Text style={[styles.planBadgeText, { color: plan.color }]}>{plan.label}</Text>
        </View>
      </View>

      <View style={styles.subDivider} />

      {loading ? (
        <ActivityIndicator size="small" color={C.green} />
      ) : error ? (
        <View style={styles.statusRow}>
          <Text style={[styles.statusDot, { color: C.amber }]}>●</Text>
          <Text style={styles.statusText}>{error}</Text>
          <TouchableOpacity onPress={fetchStatus}>
            <Text style={[styles.statusText, { color: C.green, marginLeft: 8 }]}>Retry</Text>
          </TouchableOpacity>
        </View>
      ) : (
        <>
          <View style={styles.statusRow}>
            <Text style={[styles.statusDot, { color: trialBadgeColor }]}>●</Text>
            <Text style={styles.statusText}>{trialText}</Text>
          </View>
          {!isPaid && (
            <View style={[styles.upgradeBar, { borderColor: isExpired ? C.red + "40" : C.amber + "40", backgroundColor: isExpired ? C.red + "08" : C.amber + "08" }]}>
              <Text style={[styles.upgradeText, { color: isExpired ? C.red : C.amber }]}>
                {isExpired ? "Trial expired — contact Vula to reactivate." : `Upgrade to keep access after your trial. ${plan.price}`}
              </Text>
            </View>
          )}
          {plan.price ? (
            <Text style={styles.priceHint}>{plan.label} — {plan.price}</Text>
          ) : null}
        </>
      )}
    </View>
  );
}

export default function Settings() {
  const router = useRouter();
  const [host, setHostInput] = useState("");
  const [apiKey, setApiKeyInput] = useState("");
  const [session, setSession] = useState(null);
  const [saved, setSaved] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    const [h, k, s] = await Promise.all([
      AsyncStorage.getItem("vula_host"),
      AsyncStorage.getItem("vula_api_key"),
      getSession(),
    ]);
    if (h) setHostInput(h);
    if (k) setApiKeyInput(k);
    setSession(s);
  }, []);

  useEffect(() => { load(); }, [load]);

  const onRefresh = async () => {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  };

  const save = async () => {
    const trimmed = host.trim();
    if (!trimmed.startsWith("http")) {
      Alert.alert("Invalid URL", "Host must start with http:// or https://");
      return;
    }
    await setHost(trimmed);
    await setApiKey(apiKey.trim());
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  const logout = () => {
    Alert.alert("Sign out", "You will need to sign in again.", [
      { text: "Cancel", style: "cancel" },
      {
        text: "Sign out", style: "destructive",
        onPress: async () => { await clearSession(); router.replace("/login"); },
      },
    ]);
  };

  return (
    <ScrollView
      style={styles.scroll}
      contentContainerStyle={styles.container}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={C.green} />}
    >
      {session && (
        <>
          <Text style={styles.sectionLabel}>Subscription</Text>
          <SubscriptionCard session={session} />
          <TouchableOpacity onPress={logout} style={styles.logoutBtn}>
            <Text style={styles.logoutText}>Sign out</Text>
          </TouchableOpacity>
          <View style={styles.divider} />
        </>
      )}

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
      <Text style={styles.hint}>Leave blank for local dev mode.</Text>
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
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  scroll: { flex: 1, backgroundColor: C.bg },
  container: { padding: 24, paddingBottom: 48 },
  sectionLabel: { fontSize: 12, fontWeight: "700", color: C.text, marginBottom: 8, textTransform: "uppercase", letterSpacing: 0.5 },
  hint: { fontSize: 12, color: C.muted, marginBottom: 12 },
  input: { backgroundColor: C.surface, borderColor: C.border, borderWidth: 1, borderRadius: 10, padding: 14, fontSize: 14, color: C.text, marginBottom: 8 },
  btn: { backgroundColor: C.green, borderRadius: 10, padding: 14, alignItems: "center", marginTop: 4 },
  btnText: { color: "#fff", fontSize: 14, fontWeight: "600" },
  divider: { height: 1, backgroundColor: C.border, marginVertical: 28 },
  preset: { padding: 12, backgroundColor: C.surface, borderColor: C.border, borderWidth: 1, borderRadius: 8, marginBottom: 8 },
  presetText: { fontSize: 13, color: C.text, fontFamily: "monospace" },
  // Subscription card
  subCard: { backgroundColor: C.surface, borderRadius: 12, padding: 16, borderWidth: 1, borderColor: C.border, marginBottom: 12 },
  subHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "flex-start" },
  companyName: { fontSize: 15, fontWeight: "700", color: C.text },
  emailText: { fontSize: 12, color: C.muted, marginTop: 2 },
  planBadge: { borderRadius: 6, borderWidth: 1, paddingHorizontal: 10, paddingVertical: 4, marginLeft: 12 },
  planBadgeText: { fontSize: 11, fontWeight: "700", textTransform: "uppercase", letterSpacing: 0.5 },
  subDivider: { height: 1, backgroundColor: C.border, marginVertical: 12 },
  statusRow: { flexDirection: "row", alignItems: "center" },
  statusDot: { fontSize: 10, marginRight: 6 },
  statusText: { fontSize: 13, color: C.text },
  upgradeBar: { marginTop: 10, borderRadius: 8, borderWidth: 1, padding: 10 },
  upgradeText: { fontSize: 12, lineHeight: 17 },
  priceHint: { fontSize: 11, color: C.muted, marginTop: 8 },
  logoutBtn: { backgroundColor: `${C.red}12`, borderRadius: 10, padding: 14, alignItems: "center", borderWidth: 1, borderColor: `${C.red}30` },
  logoutText: { color: C.red, fontSize: 14, fontWeight: "600" },
});
