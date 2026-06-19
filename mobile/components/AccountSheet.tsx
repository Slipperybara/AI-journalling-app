import { useState } from 'react';
import { ActivityIndicator, Alert, Modal, Pressable, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { deleteAccount } from '../lib/account';
import { useAuth } from '../lib/auth';
import { colors, fonts } from '../lib/theme';

// Bottom-sheet account menu (slides up from the bottom). Two actions: log out,
// and permanently delete the account. Both return the user to the login screen
// — signing out clears the session, which Root reacts to automatically.
export function AccountSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { signOut } = useAuth();
  const [busy, setBusy] = useState(false);

  const logout = async () => {
    onClose();
    await signOut();
  };

  const runDelete = async () => {
    setBusy(true);
    const ok = await deleteAccount();
    setBusy(false);
    if (!ok) {
      Alert.alert('Could not delete', 'Something went wrong deleting your account. Please try again.');
      return;
    }
    onClose();
    await signOut();
  };

  const confirmDelete = () => {
    Alert.alert(
      'Delete account?',
      'This permanently erases your chats, reflections and dashboard. It can’t be undone.\n\n' +
        'It does NOT cancel your subscription — to stop billing, cancel it in Settings › Apple ID › Subscriptions.',
      [
        { text: 'Cancel', style: 'cancel' },
        { text: 'Delete account', style: 'destructive', onPress: runDelete },
      ],
    );
  };

  return (
    <Modal visible={open} transparent animationType="slide" onRequestClose={onClose}>
      <Pressable
        style={{ flex: 1, backgroundColor: 'rgba(20,18,15,0.32)', justifyContent: 'flex-end' }}
        onPress={busy ? undefined : onClose}
      >
        <Pressable
          onPress={(e) => e.stopPropagation()}
          style={{ backgroundColor: colors.paper, borderTopLeftRadius: 24, borderTopRightRadius: 24 }}
        >
          <SafeAreaView edges={['bottom']}>
            <View style={{ paddingHorizontal: 22, paddingTop: 14, paddingBottom: 10 }}>
              <View
                style={{
                  alignSelf: 'center',
                  width: 38,
                  height: 4,
                  borderRadius: 2,
                  backgroundColor: '#DDD8D0',
                  marginBottom: 18,
                }}
              />
              <Text style={{ fontFamily: fonts.serifMedium, fontSize: 22, color: '#2A2825' }}>Account</Text>
              <Text style={{ fontFamily: fonts.serif, fontSize: 15, lineHeight: 22, color: '#6E6B64', marginTop: 6 }}>
                Manage your session and account.
              </Text>

              <Pressable
                onPress={logout}
                disabled={busy}
                style={{
                  height: 52,
                  borderRadius: 16,
                  borderWidth: 1,
                  borderColor: '#DDD8D0',
                  alignItems: 'center',
                  justifyContent: 'center',
                  marginTop: 20,
                  opacity: busy ? 0.5 : 1,
                }}
              >
                <Text style={{ fontFamily: fonts.sansMedium, fontSize: 15, color: '#38342F' }}>Log out</Text>
              </Pressable>

              <Pressable
                onPress={confirmDelete}
                disabled={busy}
                style={{ height: 52, borderRadius: 16, alignItems: 'center', justifyContent: 'center', marginTop: 10 }}
              >
                {busy ? (
                  <ActivityIndicator color="#B23B3B" />
                ) : (
                  <Text style={{ fontFamily: fonts.sansMedium, fontSize: 15, color: '#B23B3B' }}>Delete account</Text>
                )}
              </Pressable>
            </View>
          </SafeAreaView>
        </Pressable>
      </Pressable>
    </Modal>
  );
}
