import { useState, useEffect } from "react";
import { View, Text, TextInput, TouchableOpacity, ScrollView, ActivityIndicator, StyleSheet } from "react-native";
import { useRouter } from "expo-router";
import { askQuestion, checkStatus, getSession } from "../api/vula";

const C = {
  bg: "#F7F4EE",
  surface: "#FFFFFF",
  green: "#2C5545",
  amber: "#C4861A",
  border: "#DDD8CE",
  text: "#2A2A2A",
  muted: "#8A8680",
};

export default function HomeScreen({ headerRight } = {}) {
  const router = useRouter();
  const [session, setSession] = useState(null);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState(null);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState("checking");

  useEffect(() => {
    getSession().then(setSession);
    checkStatus()
      .then((d) => setStatus(d.status))
      .catch(() => setStatus("offline"));
  }, []);

  const tenantId = session?.tenant_id ?? "default";

  const ask = async () => {
    if (!question.trim()) return;
    setLoading(true);
    setAnswer(null);
    try {
      const data = await askQuestion(tenantId, question);
      setAnswer(data);
    } catch (e) {
      setAnswer({ answer: `Connection error: ${e.message}`, sources: [] });
    }
    setLoading(false);
  };

  const suggested = [
    "What do we charge for drawings?",
    "What are our payment terms?",
    "Summarise our services",
  ];

  return (
    <ScrollView style={styles.container} keyboardShouldPersistTaps="handled">
      {/* Header */}
      <View style={styles.header}>
        <Text style={styles.logo}>Vula</Text>
        <View style={[styles.dot, { backgroundColor: status === "ok" ? "#22C55E" : status === "offline" ? "#EF4444" : "#F59E0B" }]} />
        <Text style={styles.statusText}>{status === "ok" ? "Connected" : status === "offline" ? "Offline" : "..."}</Text>
        {headerRight && <View style={{ marginLeft: "auto" }}>{headerRight}</View>}
      </View>

      {session?.company_name ? (
        <Text style={styles.companyName}>{session.company_name}</Text>
      ) : null}
      <Text style={styles.tagline}>Ask your business AI</Text>

      {/* Suggested */}
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.chips}>
        {suggested.map((s) => (
          <TouchableOpacity key={s} onPress={() => { setQuestion(s); }} style={styles.chip}>
            <Text style={styles.chipText}>{s}</Text>
          </TouchableOpacity>
        ))}
      </ScrollView>

      {/* Quick actions */}
      <View style={styles.quickRow}>
        <TouchableOpacity onPress={() => router.push("/documents")} style={[styles.quickBtn, { flex: 1 }]}>
          <Text style={styles.quickIcon}>📂</Text>
          <Text style={styles.quickTitle}>Documents</Text>
          <Text style={styles.quickSub}>Upload & manage</Text>
        </TouchableOpacity>
        <TouchableOpacity onPress={() => router.push("/chat")} style={[styles.quickBtn, { flex: 1 }]}>
          <Text style={styles.quickIcon}>💬</Text>
          <Text style={styles.quickTitle}>AI Chat</Text>
          <Text style={styles.quickSub}>Full conversation</Text>
        </TouchableOpacity>
      </View>

      {/* Input */}
      <View style={styles.inputRow}>
        <TextInput
          style={styles.input}
          value={question}
          onChangeText={setQuestion}
          placeholder="Ask anything about your business..."
          placeholderTextColor={C.muted}
          multiline
          returnKeyType="send"
          onSubmitEditing={ask}
        />
        <TouchableOpacity onPress={ask} disabled={loading || !question.trim()} style={[styles.sendBtn, (!question.trim() || loading) && styles.sendBtnDisabled]}>
          <Text style={styles.sendBtnText}>{loading ? "..." : "→"}</Text>
        </TouchableOpacity>
      </View>

      {/* Loading */}
      {loading && <ActivityIndicator color={C.green} style={{ marginTop: 24 }} />}

      {/* Answer */}
      {answer && (
        <View style={styles.answerCard}>
          <Text style={styles.answerLabel}>Answer</Text>
          <Text style={styles.answerText}>{answer.answer}</Text>
          {answer.sources?.length > 0 && (
            <View style={styles.sources}>
              <Text style={styles.sourcesLabel}>Sources</Text>
              {answer.sources.map((s, i) => (
                <Text key={i} style={styles.sourceItem}>{s.filename} · p{s.page}</Text>
              ))}
            </View>
          )}
        </View>
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: C.bg, padding: 20 },
  header: { flexDirection: "row", alignItems: "center", marginBottom: 8, marginTop: 16 },
  logo: { fontSize: 28, fontWeight: "700", color: C.text, marginRight: 12 },
  dot: { width: 8, height: 8, borderRadius: 4, marginRight: 6 },
  statusText: { fontSize: 12, color: C.muted },
  companyName: { fontSize: 13, fontWeight: "700", color: C.green, marginBottom: 2 },
  tagline: { fontSize: 14, color: C.muted, marginBottom: 20 },
  chips: { marginBottom: 20 },
  chip: { backgroundColor: C.surface, borderColor: C.border, borderWidth: 1, borderRadius: 20, paddingHorizontal: 14, paddingVertical: 8, marginRight: 8 },
  chipText: { fontSize: 12, color: C.text },
  inputRow: { flexDirection: "row", gap: 10, marginBottom: 16 },
  input: { flex: 1, backgroundColor: C.surface, borderColor: C.border, borderWidth: 1, borderRadius: 10, padding: 14, fontSize: 14, color: C.text, minHeight: 48 },
  sendBtn: { backgroundColor: C.green, borderRadius: 10, paddingHorizontal: 20, justifyContent: "center" },
  sendBtnDisabled: { backgroundColor: C.border },
  sendBtnText: { color: "#fff", fontSize: 20, fontWeight: "600" },
  quickRow: { flexDirection: "row", gap: 12, marginBottom: 20 },
  quickBtn: { backgroundColor: C.surface, borderColor: C.border, borderWidth: 1, borderRadius: 10, padding: 14, alignItems: "center" },
  quickIcon: { fontSize: 24, marginBottom: 4 },
  quickTitle: { fontSize: 13, fontWeight: "700", color: C.text },
  quickSub: { fontSize: 11, color: C.muted, marginTop: 2 },
  answerCard: { backgroundColor: C.surface, borderColor: C.border, borderWidth: 1, borderRadius: 12, padding: 16, marginTop: 8 },
  answerLabel: { fontSize: 10, color: C.green, letterSpacing: 1, marginBottom: 10, textTransform: "uppercase" },
  answerText: { fontSize: 15, color: C.text, lineHeight: 24 },
  sources: { marginTop: 12, paddingTop: 12, borderTopWidth: 1, borderTopColor: C.border },
  sourcesLabel: { fontSize: 10, color: C.muted, marginBottom: 6, textTransform: "uppercase" },
  sourceItem: { fontSize: 11, color: C.muted, marginBottom: 2 },
});
