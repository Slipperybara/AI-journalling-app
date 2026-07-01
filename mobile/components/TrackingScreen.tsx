import { Feather } from '@expo/vector-icons';
import { useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, ScrollView, Text, TextInput, View } from 'react-native';

import { getCatalog, getTracking, saveTracking, type CatalogItem } from '../lib/tracking';
import { colors, fonts } from '../lib/theme';

function SectionLabel({ children }: { children: string }) {
  return (
    <Text
      className="uppercase text-muted"
      style={{ fontFamily: fonts.sans, fontSize: 11, letterSpacing: 1.2, marginBottom: 12 }}
    >
      {children}
    </Text>
  );
}

export function TrackingScreen() {
  const [catalog, setCatalog] = useState<CatalogItem[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [customs, setCustoms] = useState<string[]>([]);
  const [draft, setDraft] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    (async () => {
      const [cat, current] = await Promise.all([getCatalog(), getTracking()]);
      setCatalog(cat);
      setSelected(new Set(current.filter((f) => f.kind === 'preset').map((f) => f.field_key)));
      setCustoms(current.filter((f) => f.kind === 'custom').map((f) => f.name));
      setLoading(false);
    })();
  }, []);

  const togglePreset = (key: string) => {
    setSaved(false);
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });
  };

  const addCustom = () => {
    const name = draft.trim();
    if (!name) return;
    setSaved(false);
    // Case-insensitive de-dup against existing customs and preset labels.
    const lower = name.toLowerCase();
    const clashesPreset = catalog.some((c) => c.label.toLowerCase() === lower);
    if (!clashesPreset && !customs.some((c) => c.toLowerCase() === lower)) {
      setCustoms((prev) => [...prev, name]);
    }
    setDraft('');
  };

  const removeCustom = (name: string) => {
    setSaved(false);
    setCustoms((prev) => prev.filter((c) => c !== name));
  };

  const save = async () => {
    setSaving(true);
    const res = await saveTracking([...selected], customs);
    setSaving(false);
    if (res) setSaved(true);
  };

  if (loading) {
    return (
      <View className="flex-1 items-center justify-center bg-paper">
        <ActivityIndicator color={colors.muted} />
      </View>
    );
  }

  return (
    <ScrollView
      className="flex-1 bg-paper"
      contentContainerStyle={{ paddingHorizontal: 20, paddingTop: 12, paddingBottom: 44 }}
      keyboardShouldPersistTaps="handled"
    >
      <Text style={{ fontFamily: fonts.serifMedium, fontSize: 26, color: '#2A2825', marginBottom: 8 }}>
        What should JAI track?
      </Text>
      <Text style={{ fontFamily: fonts.sans, fontSize: 15, lineHeight: 22, color: '#8E8B84', marginBottom: 28 }}>
        JAI already follows your mood, sleep and focus. Pick anything else you&apos;d like it to gently
        notice — or add your own.
      </Text>

      <SectionLabel>Suggested</SectionLabel>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 10, marginBottom: 32 }}>
        {catalog.map((c) => {
          const sel = selected.has(c.key);
          return (
            <Pressable
              key={c.key}
              onPress={() => togglePreset(c.key)}
              style={{
                borderWidth: 1.5,
                borderColor: sel ? '#2A2825' : '#DDD8D0',
                backgroundColor: sel ? 'rgba(42,40,37,0.04)' : 'transparent',
                borderRadius: 20,
                paddingVertical: 10,
                paddingHorizontal: 16,
              }}
            >
              <Text style={{ fontFamily: sel ? fonts.sansMedium : fonts.sans, fontSize: 15, color: '#38342F' }}>
                {c.label}
              </Text>
            </Pressable>
          );
        })}
      </View>

      <SectionLabel>Your own</SectionLabel>
      {customs.length > 0 ? (
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 10, marginBottom: 4 }}>
          {customs.map((name) => (
            <Pressable
              key={name}
              onPress={() => removeCustom(name)}
              style={{
                flexDirection: 'row',
                alignItems: 'center',
                gap: 6,
                borderWidth: 1.5,
                borderColor: '#2A2825',
                backgroundColor: 'rgba(42,40,37,0.04)',
                borderRadius: 20,
                paddingVertical: 10,
                paddingHorizontal: 14,
              }}
            >
              <Text style={{ fontFamily: fonts.sansMedium, fontSize: 15, color: '#38342F' }}>{name}</Text>
              <Feather name="x" size={14} color="#8E8B84" />
            </Pressable>
          ))}
        </View>
      ) : null}

      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, marginTop: 12 }}>
        <TextInput
          value={draft}
          onChangeText={setDraft}
          placeholder="Add your own…"
          placeholderTextColor="#B4B1A9"
          onSubmitEditing={addCustom}
          returnKeyType="done"
          style={{
            flex: 1,
            fontFamily: fonts.serif,
            fontSize: 16,
            color: '#2A2825',
            borderBottomWidth: 1,
            borderBottomColor: '#DDD8D0',
            paddingVertical: 8,
          }}
        />
        <Pressable
          onPress={addCustom}
          disabled={!draft.trim()}
          style={{
            borderRadius: 14,
            backgroundColor: '#2A2825',
            paddingVertical: 10,
            paddingHorizontal: 18,
            opacity: draft.trim() ? 1 : 0.35,
          }}
        >
          <Text style={{ fontFamily: fonts.sansMedium, fontSize: 14, color: '#fff' }}>Add</Text>
        </Pressable>
      </View>

      <Pressable
        onPress={save}
        disabled={saving}
        style={{
          height: 54,
          borderRadius: 18,
          backgroundColor: '#2A2825',
          justifyContent: 'center',
          alignItems: 'center',
          marginTop: 32,
          opacity: saving ? 0.6 : 1,
        }}
      >
        <Text style={{ fontFamily: fonts.sansMedium, fontSize: 16, color: '#fff' }}>
          {saved ? 'Saved ✓' : 'Save'}
        </Text>
      </Pressable>
    </ScrollView>
  );
}
