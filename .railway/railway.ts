import { defineRailway, project, service, volume } from "railway/iac";

export default defineRailway(() => {
  const pulseData = volume("pulse-data", { alerts: { usage: { "100": {}, "80": {}, "95": {} } }, allowOnlineResize: true, region: "sfo", sizeMB: 500 });
  const pulseAShare = service("pulse-a-share", {
    build: { buildEnvironment: "V3", builder: "DOCKERFILE", dockerfilePath: "Dockerfile" },
    healthcheck: "/healthz",
    healthcheckTimeout: 180,
    deploy: { sleepApplication: false, restartPolicyType: "ON_FAILURE", restartPolicyMaxRetries: 10 },
    replicas: { "sfo": 1 },
    volumeMounts: { "/data": pulseData },
  });

  return project("pulse-a-share", {
    variables: { managed: false },
    resources: [pulseAShare, pulseData],
  });
});
