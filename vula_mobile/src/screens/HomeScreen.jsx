import { useState, useEffect } from "react";
import { View, Text, TextInput, TouchableOpacity, ScrollView, ActivityIndicator, StyleSheet } from "react-native";
import { askQuestion, checkStatus } from "../api/vula";

const TENANT_ID = "mobile_user";

const C = {
  bg: "#F7F4EE",
  surface: "#FFFFFF",
  green: "#2C5545",
  amber: "#C4861A",
  border: "#DDD8CE",
  text: "#2A2A2A",
  muted: "#8A8680",
};

export default function HomeScreen() {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState(null);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState("checking");

  useEffect(() => {
    checkStatus()
      .then((d) => setStatus(d.status))
      .catch(() => setStatus("offline"));
  }, []);

  const ask = async () => {
    if (!question.trim()) return;
    setLoading(true);
    setAnswer(null);
    try {
      const data = await askQuestion(TENANT_ID, question);
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
      </View>

      <Text style={styles.tagline}>Ask your business AI</Text>

      {/* Suggested */}
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.chips}>
        {suggested.map((s) => (
          <TouchableOpacity key={s} onPress={() => { setQuestion(s); }} style={styles.chip}>
            <Text style={styles.chipText}>{s}</Text>
          </TouchableOpacity>
        ))}
      </ScrollView>

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
  tagline: { fontSize: 14, color: C.muted, marginBottom: 20 },
  chips: { marginBottom: 20 },
  chip: { backgroundColor: C.surface, borderColor: C.border, borderWidth: 1, borderRadius: 20, paddingHorizontal: 14, paddingVertical: 8, marginRight: 8 },
  chipText: { fontSize: 12, color: C.text },
  inputRow: { flexDirection: "row", gap: 10, marginBottom: 16 },
  input: { flex: 1, backgroundColor: C.surface, borderColor: C.border, borderWidth: 1, borderRadius: 10, padding: 14, fontSize: 14, color: C.text, minHeight: 48 },
  sendBtn: { backgroundColor: C.green, borderRadius: 10, paddingHorizontal: 20, justifyContent: "center" },
  sendBtnDisabled: { backgroundColor: C.border },
  sendBtnText: { color: "#fff", fontSize: 20, fontWeight: "600" },
  answerCard: { backgroundColor: C.surface, borderColor: C.border, borderWidth: 1, borderRadius: 12, padding: 16, marginTop: 8 },
  answerLabel: { fontSize: 10, color: C.green, letterSpacing: 1, marginBottom: 10, textTransform: "uppercase" },
  answerText: { fontSize: 15, color: C.text, lineHeight: 24 },
  sources: { marginTop: 12, paddingTop: 12, borderTopWidth: 1, borderTopColor: C.border },
  sourcesLabel: { fontSize: 10, color: C.muted, marginBottom: 6, textTransform: "uppercase" },
  sourceItem: { fontSize: 11, color: C.muted, marginBottom: 2 },
});
