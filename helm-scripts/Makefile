PYTHON ?= python3
GEN    ?= $(PYTHON) generate_helm.py

.PHONY: help generate generate-strict helm helm-strict helm-json \
        helm-fail-on-drop ci \
        helm-classic helm-classic-strict \
        helm-cloudnative helm-cloudnative-strict

help:
	@echo "Targets:"
	@echo "  generate                Generate values and scripts (dynakube-driven)"
	@echo "  generate-strict         Generate with strict validation"
	@echo "  helm                    Generate for OCI (dynakube-driven profile)"
	@echo "  helm-strict             Generate OCI with strict validation"
	@echo "  helm-json               Generate OCI and write JSON report"
	@echo "  helm-fail-on-drop       Generate OCI and fail if any feature is unmapped"
	@echo "  ci                      OCI strict + JSON report + fail-on-drop (CI gate)"
	@echo "  helm-classic            Generate with classic profile override"
	@echo "  helm-classic-strict     Generate classic with strict validation"
	@echo "  helm-cloudnative        Generate with cloud-native profile override"
	@echo "  helm-cloudnative-strict Generate cloud-native with strict validation"

generate:
	$(GEN)

generate-strict:
	$(GEN) --strict

helm:
	$(GEN) --profile dynakube

helm-strict:
	$(GEN) --profile dynakube --strict

helm-json:
	$(GEN) --profile dynakube --report-json

helm-fail-on-drop:
	$(GEN) --profile dynakube --fail-on-drop

ci:
	$(GEN) --profile dynakube --strict --report-json --fail-on-drop

helm-classic:
	$(GEN) --profile classic

helm-classic-strict:
	$(GEN) --profile classic --strict

helm-cloudnative:
	$(GEN) --profile cloudnative

helm-cloudnative-strict:
	$(GEN) --profile cloudnative --strict
