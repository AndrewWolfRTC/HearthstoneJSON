import pytest
from mock import call, patch, Mock
from alarmobot import AlarmOBot
from keg.http import HttpRemote


old_version = Mock(versions_name="1.0.0.123", build_id="123", build_config="foo", region="us")
new_version = Mock(versions_name="1.0.1.234", build_id="234", build_config="bar", region="us")


def test_compare_versions():
	app = AlarmOBot(["--ngdp-bin", "", "--ngdp-dir", ""])

	with pytest.raises(ValueError) as error:
		app.compare_versions(None, new_version)
	assert str(error.value) == "old_version is not a valid version object: None"

	with pytest.raises(ValueError) as error:
		app.compare_versions(old_version, None)
	assert str(error.value) == "new_version is not a valid version object: None"

	app.simulate_new_build = True
	result = app.compare_versions(old_version, old_version)
	assert result
	assert not app.simulate_new_build

	result = app.compare_versions(old_version, old_version)
	assert not result

	result = app.compare_versions(old_version, new_version)
	assert result


@patch.object(AlarmOBot, "write_to_discord")
@patch.object(AlarmOBot, "send_email")
@patch.object(AlarmOBot, "call_ngdp", return_value=Mock(returncode=0))
def test_on_new_build(call_ngdp, send_mail, write_to_discord):
	app = AlarmOBot(["--ngdp-bin", "", "--ngdp-dir", ""])

	app.on_new_build(old_version, new_version)

	write_to_discord.assert_called_with("Successfully installed new build to %s" % new_version.build_id)
	send_mail.assert_called()
	call_ngdp.assert_has_calls([
		call(["fetch", "hsb"]),
		call(["install", "hsb", new_version.build_config, new_version.build_id])
	])


@patch.object(HttpRemote, "get_versions", return_value=[old_version, new_version])
def test_get_latest_version(get_versions):
	app = AlarmOBot(["--ngdp-bin", "", "--ngdp-dir", ""])
	assert app.get_latest_version() is new_version


@patch.object(AlarmOBot, "write_to_influx")
@patch.object(AlarmOBot, "on_new_build")
@patch.object(AlarmOBot, "get_latest_version", return_value=new_version)
def test_check_for_new_version(get_latest_version, on_new_build, write_to_influx):
	app = AlarmOBot(["--ngdp-bin", "", "--ngdp-dir", ""])

	version = app.check_for_new_version(old_version)
	assert version is new_version

	get_latest_version.assert_called()
	on_new_build.assert_called_with(old_version, new_version)
	write_to_influx.assert_called_with(new_version.versions_name)

	get_latest_version.reset_mock()
	on_new_build.reset_mock()
	write_to_influx.reset_mock()

	version = app.check_for_new_version(new_version)
	assert version is new_version

	get_latest_version.assert_called()
	on_new_build.assert_not_called()
	write_to_influx.assert_called_with(new_version.versions_name)

