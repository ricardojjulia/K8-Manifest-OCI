PYTHON ?= python3
SPLIT ?= $(PYTHON) split_manifests.py


.PHONY: help split split-strict oci oci-strict oci-json oci-fail-on-drop ci \
	oci-classic oci-classic-strict oci-cloudnative oci-cloudnative-strict

help:
	@echo "Targets:"
	@echo "  split                  Run default staged split"
	@echo "  split-strict           Run default split with strict validation"
	@echo "  oci                    Run OCI filtering using dynakube_OCI.yaml"
	@echo "  oci-strict             Run OCI filtering with strict validation"
	@echo "  oci-json               Run OCI filtering and write JSON report"
	@echo "  oci-fail-on-drop       Run OCI filtering and fail if anything is dropped"
	@echo "  ci                     OCI strict + JSON report + fail-on-drop (CI gate)"
	@echo "  oci-classic            Run OCI filtering with classic profile override"
	@echo "  oci-classic-strict     Run OCI classic profile with strict validation"
	@echo "  oci-cloudnative        Run OCI filtering with cloud-native profile override"
	@echo "  oci-cloudnative-strict Run OCI cloud-native profile with strict validation"

split:
	$(SPLIT)

split-strict:
	$(SPLIT) --strict

oci:
	$(SPLIT) --process4oci

oci-strict:
	$(SPLIT) --process4oci --strict

oci-json:
	$(SPLIT) --process4oci --report-json

oci-fail-on-drop:
	$(SPLIT) --process4oci --fail-on-drop

ci:
	$(SPLIT) --process4oci --strict --report-json --fail-on-drop

oci-classic:
	$(SPLIT) --process4classic

oci-classic-strict:
	$(SPLIT) --process4classic --strict

oci-cloudnative:
	$(SPLIT) --process4cloudnative

oci-cloudnative-strict:
	$(SPLIT) --process4cloudnative --strict
