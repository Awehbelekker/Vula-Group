import { useState, useCallback } from "react";
import {
  View, Text, TouchableOpacity, ScrollView, ActivityIndicator,
  Alert, StyleSheet,
} from "react-native";
import * as DocumentPicker from "expo-document-picker";
import { uploadDocument } from "../api/vula";

const TENANT_ID = "mobile_user";

const C = {
  bg: "#F7F4EE", surface: "#FFFFFF", green: "#2C5545",
  amber: "#C4861A", red: "#C0392B",
  border: "#DDD8CE", text: "#2A2A2A", muted: "#8A8680",
};

const EXT_COLOR = {
  pdf: "#C0392B", docx: "#2B5797", doc: "#2B5797",
  xlsx: "#1E7145", xls: "#1E7145", csv: "#1E7145",
};

function FileBadge({ name }) {
  const ext = name.split(".").pop().toLowerCase();
  const color = EXT_COLOR[ext] || C.muted;
  return (
    <View style={[styles.badge, { backgroundColor: `${color}22` }]}>
      <Text style={[styles.badgeText, { color }]}>{ext.toUpperCase()}</Text>
    </View>
  );
}

function StatusDot({ status }) {
  const colors = { pending: C.muted, uploading: C.amber, done: "#22C55E", error: C.red };
  const labels = { pending: "Pending", uploading: "Uploading…", done: "Queued", error: "Failed" };
  return (
    <Text style={{ fontSize: 11, color: colors[status] || C.muted, fontWeight: "600" }}>
      {labels[status] || status}
    </Text>
  );
}

export default function DocumentsScreen() {
  const [queue, setQueue] = useState([]);
  const [uploading, setUploading] = useState(false);

  const pick = useCallback(async () => {
    try {
      const result = await DocumentPicker.getDocumentAsync({
        type: [
          "application/pdf",
          "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          "text/plain", "text/csv",
          "image/*",
        ],
        multiple: true,
        copyToCacheDirectory: true,
      });

      if (result.canceled) return;

      const files = result.assets || [];
      const newItems = files.map((f) => ({
        uri: f.uri,
        name: f.name,
        mimeType: f.mimeType || "application/octet-stream",
        size: f.size || 0,
        status: "pending",
      }));
      setQueue((q) => [...q, ...newItems]);
    } catch {
      Alert.alert("Error", "Could not open file picker.");
    }
  }, []);

  const uploadAll = useCallback(async () => {
    const pending = queue.filter((q) => q.status === "pending");
    if (!pending.length || uploading) return;
    setUploading(true);

    for (const item of pending) {
      setQueue((q) => q.map((x) => x.uri === item.uri ? { ...x, status: "uploading" } : x));
      try {
        await uploadDocument(TENANT_ID, item);
        setQueue((q) => q.map((x) => x.uri === item.uri ? { ...x, status: "done" } : x));
      } catch {
        setQueue((q) => q.map((x) => x.uri === item.uri ? { ...x, status: "error" } : x));
      }
    }

    setUploading(false);
    const failed = queue.filter((q) => q.status === "error").length;
    if (failed === 0) {
      Alert.alert("Upload complete", "Your documents are queued for processing. Your AI will have them ready in 2–5 minutes.");
    }
  }, [queue, uploading]);

  const clearDone = () => setQueue((q) => q.filter((x) => x.status !== "done"));
  const pendingCount = queue.filter((q) => q.status === "pending").length;

  return (
    <ScrollView style={styles.container}>
      <View style={styles.inner}>
        {/* Description */}
        <View style={styles.infoBox}>
          <Text style={styles.infoTitle}>Upload your documents</Text>
          <Text style={styles.infoText}>
            Add price lists, SOPs, proposals, or any document you want your AI to know about.
            Processing takes 2–5 minutes.
          </Text>
        </View>

        {/* Pick button */}
        <TouchableOpacity onPress={pick} style={styles.pickBtn} disabled={uploading}>
          <Text style={styles.pickBtnText}>+ Add Documents</Text>
        </TouchableOpacity>

        {/* Queue */}
        {queue.length > 0 && (
          <View style={styles.queueContainer}>
            <Text style={styles.queueLabel}>
              {queue.length} {queue.length === 1 ? "file" : "files"} selected
            </Text>
            {queue.map((item, i) => (
              <View key={i} style={styles.queueItem}>
                <FileBadge name={item.name} />
                <View style={{ flex: 1, marginLeft: 10 }}>
                  <Text style={styles.fileName} numberOfLines={1}>{item.name}</Text>
                  <Text style={styles.fileSize}>
                    {item.size > 0 ? `${Math.round(item.size / 1024)} KB` : ""}
                  </Text>
                </View>
                <StatusDot status={item.status} />
              </View>
            ))}
          </View>
        )}

        {/* Upload button */}
        {pendingCount > 0 && (
          <TouchableOpacity
            onPress={uploadAll}
            style={[styles.uploadBtn, uploading && styles.uploadBtnDisabled]}
            disabled={uploading}
          >
            {uploading
              ? <ActivityIndicator color="#fff" size="small" />
              : <Text style={styles.uploadBtnText}>
                  Upload {pendingCount} file{pendingCount > 1 ? "s" : ""}
                </Text>
            }
          </TouchableOpacity>
        )}

        {/* Clear done */}
        {queue.some((q) => q.status === "done") && !uploading && (
          <TouchableOpacity onPress={clearDone} style={styles.clearBtn}>
            <Text style={styles.clearBtnText}>Clear completed</Text>
          </TouchableOpacity>
        )}

        {/* Supported formats */}
        <Text style={styles.hint}>
          Supported: PDF, DOCX, XLSX, CSV, TXT, PNG, JPG — max 50 MB each
        </Text>
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: C.bg },
  inner: { padding: 20 },
  infoBox: {
    backgroundColor: `${C.green}10`,
    borderRadius: 10,
    padding: 16,
    marginBottom: 20,
    borderWidth: 1,
    borderColor: `${C.green}30`,
  },
  infoTitle: { fontSize: 14, fontWeight: "700", color: C.green, marginBottom: 6 },
  infoText: { fontSize: 13, color: C.text, lineHeight: 20 },
  pickBtn: {
    backgroundColor: C.surface,
    borderWidth: 2,
    borderColor: C.border,
    borderStyle: "dashed",
    borderRadius: 10,
    paddingVertical: 20,
    alignItems: "center",
    marginBottom: 20,
  },
  pickBtnText: { fontSize: 15, color: C.green, fontWeight: "600" },
  queueContainer: {
    backgroundColor: C.surface,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: C.border,
    overflow: "hidden",
    marginBottom: 16,
  },
  queueLabel: {
    fontSize: 11, fontWeight: "700", color: C.muted,
    textTransform: "uppercase", letterSpacing: 0.5,
    padding: 12, borderBottomWidth: 1, borderBottomColor: C.border,
  },
  queueItem: {
    flexDirection: "row", alignItems: "center",
    padding: 14,
    borderBottomWidth: 1, borderBottomColor: C.border,
  },
  badge: { paddingHorizontal: 6, paddingVertical: 3, borderRadius: 4, minWidth: 38, alignItems: "center" },
  badgeText: { fontSize: 10, fontWeight: "700" },
  fileName: { fontSize: 13, color: C.text },
  fileSize: { fontSize: 11, color: C.muted, marginTop: 2 },
  uploadBtn: {
    backgroundColor: C.green, borderRadius: 10,
    paddingVertical: 14, alignItems: "center", marginBottom: 12,
  },
  uploadBtnDisabled: { backgroundColor: C.muted },
  uploadBtnText: { color: "#fff", fontSize: 14, fontWeight: "700" },
  clearBtn: { alignItems: "center", paddingVertical: 10 },
  clearBtnText: { fontSize: 13, color: C.muted },
  hint: { fontSize: 12, color: C.muted, textAlign: "center", marginTop: 20 },
});
