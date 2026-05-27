import { useState, useEffect, useRef, useCallback } from "react";
import {
  View, Text, TextInput, TouchableOpacity, FlatList,
  ActivityIndicator, Alert, StyleSheet, KeyboardAvoidingView, Platform,
} from "react-native";
import { sendChatMessage, getChatHistory, clearChatHistory, getSession } from "../api/vula";

const C = {
  bg: "#F7F4EE",
  surface: "#FFFFFF",
  green: "#2C5545",
  greenLight: "#E8F0EC",
  amber: "#C4861A",
  border: "#DDD8CE",
  text: "#2A2A2A",
  muted: "#8A8680",
};

function Bubble({ msg }) {
  const isUser = msg.role === "user";
  return (
    <View style={[styles.bubbleWrap, isUser ? styles.bubbleRight : styles.bubbleLeft]}>
      {!isUser && <Text style={styles.bubbleSender}>Vula AI</Text>}
      <View style={[styles.bubble, isUser ? styles.bubbleUser : styles.bubbleAI]}>
        <Text style={[styles.bubbleText, isUser && styles.bubbleTextUser]}>{msg.text}</Text>
      </View>
      <Text style={styles.bubbleTime}>{new Date(msg.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</Text>
    </View>
  );
}

export default function ChatScreen() {
  const [session, setSession] = useState(null);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [loading, setLoading] = useState(true);
  const listRef = useRef(null);

  const tenantId = session?.tenant_id ?? "default";

  useEffect(() => {
    getSession().then(setSession);
  }, []);

  useEffect(() => {
    if (!session) return;
    loadHistory();
  }, [session]);

  const loadHistory = async () => {
    setLoading(true);
    try {
      const data = await getChatHistory(tenantId);
      setMessages(data.messages || []);
    } catch {
      // Silent fail — history is optional
    } finally {
      setLoading(false);
    }
  };

  const send = useCallback(async () => {
    const text = input.trim();
    if (!text || sending) return;
    setInput("");
    setSending(true);

    const tempId = Date.now().toString();
    const userMsg = { role: "user", text, created_at: new Date().toISOString(), _id: tempId };
    setMessages((prev) => [...prev, userMsg]);

    try {
      const data = await sendChatMessage(tenantId, text);
      const aiMsg = { role: "assistant", text: data.reply, created_at: new Date().toISOString(), _id: `${tempId}_ai` };
      setMessages((prev) => [...prev, aiMsg]);
    } catch (e) {
      const errMsg = { role: "assistant", text: `Connection error: ${e.message}`, created_at: new Date().toISOString(), _id: `${tempId}_err` };
      setMessages((prev) => [...prev, errMsg]);
    } finally {
      setSending(false);
      setTimeout(() => listRef.current?.scrollToEnd({ animated: true }), 100);
    }
  }, [input, sending, tenantId]);

  const confirmClear = () => {
    Alert.alert("Clear History", "Delete all conversation history?", [
      { text: "Cancel", style: "cancel" },
      {
        text: "Clear", style: "destructive", onPress: async () => {
          try {
            await clearChatHistory(tenantId);
            setMessages([]);
          } catch (e) {
            Alert.alert("Error", e.message);
          }
        },
      },
    ]);
  };

  return (
    <KeyboardAvoidingView
      style={styles.container}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
      keyboardVerticalOffset={88}
    >
      {/* Toolbar */}
      <View style={styles.toolbar}>
        <Text style={styles.toolbarTitle}>AI Assistant</Text>
        {messages.length > 0 && (
          <TouchableOpacity onPress={confirmClear}>
            <Text style={styles.clearBtn}>Clear</Text>
          </TouchableOpacity>
        )}
      </View>

      {/* Messages */}
      {loading ? (
        <ActivityIndicator color={C.green} style={{ marginTop: 40 }} />
      ) : (
        <FlatList
          ref={listRef}
          data={messages}
          keyExtractor={(m, i) => m._id || `${i}`}
          renderItem={({ item }) => <Bubble msg={item} />}
          contentContainerStyle={styles.messageList}
          ListEmptyComponent={
            <View style={styles.empty}>
              <Text style={styles.emptyTitle}>Ask your Vula AI</Text>
              <Text style={styles.emptyText}>
                Ask anything about your business — pricing, quotes, services, documents.
              </Text>
            </View>
          }
          onContentSizeChange={() => listRef.current?.scrollToEnd({ animated: false })}
        />
      )}

      {/* Typing indicator */}
      {sending && (
        <View style={styles.typingRow}>
          <ActivityIndicator size="small" color={C.green} />
          <Text style={styles.typingText}>Vula AI is thinking…</Text>
        </View>
      )}

      {/* Input */}
      <View style={styles.inputRow}>
        <TextInput
          style={styles.input}
          value={input}
          onChangeText={setInput}
          placeholder="Type a message…"
          placeholderTextColor={C.muted}
          multiline
          maxLength={2000}
          returnKeyType="send"
          blurOnSubmit
          onSubmitEditing={send}
        />
        <TouchableOpacity
          onPress={send}
          disabled={!input.trim() || sending}
          style={[styles.sendBtn, (!input.trim() || sending) && styles.sendBtnOff]}
        >
          <Text style={styles.sendIcon}>↑</Text>
        </TouchableOpacity>
      </View>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: C.bg },
  toolbar: {
    flexDirection: "row", justifyContent: "space-between", alignItems: "center",
    paddingHorizontal: 16, paddingVertical: 10,
    borderBottomWidth: 1, borderBottomColor: C.border, backgroundColor: C.surface,
  },
  toolbarTitle: { fontSize: 14, fontWeight: "700", color: C.text },
  clearBtn: { fontSize: 13, color: C.amber },
  messageList: { padding: 16, paddingBottom: 8 },
  empty: { alignItems: "center", paddingTop: 60, paddingHorizontal: 32 },
  emptyTitle: { fontSize: 18, fontWeight: "700", color: C.green, marginBottom: 10 },
  emptyText: { fontSize: 14, color: C.muted, textAlign: "center", lineHeight: 22 },
  bubbleWrap: { marginBottom: 12 },
  bubbleLeft: { alignItems: "flex-start" },
  bubbleRight: { alignItems: "flex-end" },
  bubbleSender: { fontSize: 10, color: C.muted, marginBottom: 3, textTransform: "uppercase", letterSpacing: 0.5 },
  bubble: { borderRadius: 16, paddingVertical: 10, paddingHorizontal: 14, maxWidth: "80%" },
  bubbleUser: { backgroundColor: C.green, borderBottomRightRadius: 4 },
  bubbleAI: { backgroundColor: C.surface, borderWidth: 1, borderColor: C.border, borderBottomLeftRadius: 4 },
  bubbleText: { fontSize: 14, color: C.text, lineHeight: 21 },
  bubbleTextUser: { color: "#FFFFFF" },
  bubbleTime: { fontSize: 10, color: C.muted, marginTop: 3 },
  typingRow: { flexDirection: "row", alignItems: "center", gap: 8, paddingHorizontal: 16, paddingBottom: 6 },
  typingText: { fontSize: 12, color: C.muted, fontStyle: "italic" },
  inputRow: {
    flexDirection: "row", gap: 10, padding: 12,
    borderTopWidth: 1, borderTopColor: C.border, backgroundColor: C.surface,
  },
  input: {
    flex: 1, backgroundColor: C.bg, borderRadius: 20, paddingHorizontal: 16,
    paddingVertical: 10, fontSize: 14, color: C.text, maxHeight: 120,
    borderWidth: 1, borderColor: C.border,
  },
  sendBtn: {
    width: 44, height: 44, borderRadius: 22, backgroundColor: C.green,
    justifyContent: "center", alignItems: "center",
  },
  sendBtnOff: { backgroundColor: C.border },
  sendIcon: { fontSize: 20, color: "#fff", fontWeight: "700" },
});
