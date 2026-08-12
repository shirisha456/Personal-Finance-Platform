{{/*
Fixed name, not release-name-prefixed — this chart is always installed
as exactly one release named "core-api" in the personal-finance-platform namespace, so
there's no need for the usual "multiple releases of the same chart"
templating.
*/}}
{{- define "core-api.fullname" -}}
{{ .Chart.Name }}
{{- end }}

{{- define "core-api.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "core-api.selectorLabels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}
