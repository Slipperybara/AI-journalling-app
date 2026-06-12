import { useEffect, useState } from 'react';
import { ActivityIndicator, RefreshControl, ScrollView, Text, View } from 'react-native';

import { getDashboard, type DashboardData } from '../lib/dashboard';
import { avg, emotionalScore, fmtScore, last7Days, physicalScore, weekdayShort } from '../lib/scoring';
import { colors, fonts } from '../lib/theme';

const CHART_H = 88;

function DimensionBars({
  title,
  color,
  days,
  scoreByDay,
  headline,
  subtitle,
}: {
  title: string;
  color: string;
  days: string[];
  scoreByDay: Record<string, number>;
  headline: string;
  subtitle?: string;
}) {
  return (
    <View className="mb-7">
      <View className="mb-2 flex-row items-baseline justify-between">
        <Text
          className="uppercase text-muted"
          style={{ fontFamily: fonts.sans, fontSize: 11, letterSpacing: 1.2 }}
        >
          {title}
        </Text>
        <Text style={{ fontSize: 13, color: '#4A4842' }}>
          <Text style={{ fontFamily: fonts.sansMedium }}>{headline}</Text>
          <Text style={{ fontFamily: fonts.sans, color: '#B7B4AD' }}>/100</Text>
          {subtitle ? (
            <Text style={{ fontFamily: fonts.sans, color: '#B7B4AD' }}>{`   ·  ${subtitle}`}</Text>
          ) : null}
        </Text>
      </View>
      <View className="flex-row items-end gap-1.5" style={{ height: CHART_H }}>
        {days.map((d) => {
          const v = scoreByDay[d];
          const has = v != null;
          const h = has ? Math.max(4, (v / 100) * (CHART_H - 4)) : 4;
          return (
            <View key={d} className="flex-1 justify-end" style={{ height: '100%' }}>
              <View
                style={{
                  height: h,
                  backgroundColor: has ? color : colors.track,
                  borderTopLeftRadius: 5,
                  borderTopRightRadius: 5,
                }}
              />
            </View>
          );
        })}
      </View>
      <View className="mt-1 flex-row gap-1.5">
        {days.map((d) => (
          <Text key={d} className="flex-1 text-center text-faint" style={{ fontSize: 9, fontFamily: fonts.sans }}>
            {weekdayShort(d)}
          </Text>
        ))}
      </View>
    </View>
  );
}

function JournalingTracker({ week }: { week: { day: string; journaled: boolean }[] }) {
  const days = week.length ? week : last7Days().map((d) => ({ day: d, journaled: false }));
  const count = days.filter((w) => w.journaled).length;
  return (
    <View>
      <View className="mb-2 flex-row items-baseline justify-between">
        <Text
          className="uppercase text-muted"
          style={{ fontFamily: fonts.sans, fontSize: 11, letterSpacing: 1.2 }}
        >
          Journaling streak
        </Text>
        <Text style={{ fontSize: 13, color: '#4A4842' }}>
          <Text style={{ fontFamily: fonts.sansMedium }}>{count}</Text>
          <Text style={{ fontFamily: fonts.sans, color: '#B7B4AD' }}>/7 days</Text>
        </Text>
      </View>
      <View className="flex-row gap-1.5">
        {days.map((w) => (
          <View key={w.day} className="flex-1 items-center">
            <View
              style={{
                width: '100%',
                height: 30,
                borderRadius: 6,
                backgroundColor: w.journaled ? colors.journaled : colors.track,
              }}
            />
            <Text className="mt-1 text-faint" style={{ fontSize: 9, fontFamily: fonts.sans }}>
              {weekdayShort(w.day)}
            </Text>
          </View>
        ))}
      </View>
    </View>
  );
}

export function DashboardScreen() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const load = async () => {
    const d = await getDashboard();
    setData(d);
  };

  useEffect(() => {
    (async () => {
      await load();
      setLoading(false);
    })();
  }, []);

  if (loading) {
    return (
      <View className="flex-1 items-center justify-center bg-paper">
        <ActivityIndicator color={colors.muted} />
      </View>
    );
  }

  const week = data?.journaling_week ?? [];
  const days = week.length ? week.map((w) => w.day) : last7Days();

  const emoByDay: Record<string, number> = {};
  data?.emotional.forEach((r) => {
    const s = emotionalScore(r.valence, r.arousal);
    if (s != null) emoByDay[r.day] = s;
  });
  const physByDay: Record<string, number> = {};
  data?.health.forEach((r) => {
    const s = physicalScore(r);
    if (s != null) physByDay[r.day] = s;
  });

  return (
    <ScrollView
      className="flex-1 bg-paper"
      contentContainerStyle={{ paddingHorizontal: 20, paddingTop: 12, paddingBottom: 32 }}
      refreshControl={
        <RefreshControl
          refreshing={refreshing}
          onRefresh={async () => {
            setRefreshing(true);
            await load();
            setRefreshing(false);
          }}
          tintColor={colors.muted}
        />
      }
    >
      {data?.summary ? (
        <Text
          className="mb-7"
          style={{ fontFamily: fonts.serif, fontSize: 17, lineHeight: 27, color: '#56534B' }}
        >
          {data.summary}
        </Text>
      ) : null}

      <View
        style={{
          backgroundColor: colors.card,
          borderColor: colors.cardBorder,
          borderWidth: 1,
          borderRadius: 16,
          padding: 16,
          marginBottom: 28,
        }}
      >
        <JournalingTracker week={week} />
      </View>

      <DimensionBars
        title="Emotional health"
        color={colors.emotional}
        days={days}
        scoreByDay={emoByDay}
        headline={fmtScore(avg(days.map((d) => emoByDay[d])))}
      />
      <DimensionBars
        title="Exercise"
        color={colors.exercise}
        days={days}
        scoreByDay={physByDay}
        headline={fmtScore(avg(days.map((d) => physByDay[d])))}
      />
    </ScrollView>
  );
}
