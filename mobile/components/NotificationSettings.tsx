import { useEffect, useState } from 'react';
import { Modal, Pressable, Switch, Text, View } from 'react-native';

import {
  formatTime,
  getNotificationPrefs,
  loadLocalNotifyChoice,
  saveLocalNotifyChoice,
  saveNotificationPrefs,
} from '../lib/notificationPrefs';
import { registerForPushNotifications } from '../lib/notifications';
import { colors, fonts } from '../lib/theme';
import { TimePicker } from './TimePicker';

// Lets the user change their morning-reflection time after onboarding. Writes
// straight to the backend (we're authed here) and keeps the local copy in step
// so the boot-time sync doesn't clobber the change.
export function NotificationSettings({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [enabled, setEnabled] = useState(true);
  const [hour, setHour] = useState(8);
  const [minute, setMinute] = useState(0);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    (async () => {
      const remote = await getNotificationPrefs();
      const local = await loadLocalNotifyChoice();
      const src = remote ?? local;
      if (src) {
        setEnabled(src.enabled);
        setHour(src.hour);
        setMinute(src.minute);
      }
    })();
  }, [open]);

  const save = async () => {
    setSaving(true);
    if (enabled) await registerForPushNotifications();
    await Promise.all([
      saveNotificationPrefs({ enabled, hour, minute }),
      saveLocalNotifyChoice({ enabled, hour, minute }),
    ]);
    setSaving(false);
    onClose();
  };

  return (
    <Modal visible={open} transparent animationType="fade" onRequestClose={onClose}>
      <Pressable
        style={{ flex: 1, backgroundColor: 'rgba(20,18,15,0.32)', justifyContent: 'center', padding: 24 }}
        onPress={onClose}
      >
        <Pressable
          onPress={(e) => e.stopPropagation()}
          style={{ backgroundColor: colors.paper, borderRadius: 20, padding: 22 }}
        >
          <Text style={{ fontFamily: fonts.serifMedium, fontSize: 22, color: '#2A2825' }}>
            Morning reflection
          </Text>
          <Text style={{ fontFamily: fonts.serif, fontSize: 15, lineHeight: 22, color: '#6E6B64', marginTop: 6 }}>
            A gentle push each morning with a reflection on your yesterday.
          </Text>

          <View
            style={{
              flexDirection: 'row',
              alignItems: 'center',
              justifyContent: 'space-between',
              marginTop: 18,
            }}
          >
            <Text style={{ fontFamily: fonts.sansMedium, fontSize: 15, color: '#38342F' }}>
              Daily reflection
            </Text>
            <Switch value={enabled} onValueChange={setEnabled} />
          </View>

          {enabled ? (
            <View style={{ marginTop: 6 }}>
              <Text style={{ fontFamily: fonts.sans, fontSize: 13, color: '#9A9790', textAlign: 'center' }}>
                Arrives around {formatTime(hour, minute)}
              </Text>
              <TimePicker
                hour={hour}
                minute={minute}
                onChange={(h, m) => {
                  setHour(h);
                  setMinute(m);
                }}
              />
            </View>
          ) : null}

          <Pressable
            onPress={save}
            disabled={saving}
            style={{
              height: 50,
              borderRadius: 16,
              backgroundColor: '#2A2825',
              alignItems: 'center',
              justifyContent: 'center',
              marginTop: 14,
              opacity: saving ? 0.5 : 1,
            }}
          >
            <Text style={{ fontFamily: fonts.sansMedium, fontSize: 15, color: '#fff' }}>Save</Text>
          </Pressable>
        </Pressable>
      </Pressable>
    </Modal>
  );
}
