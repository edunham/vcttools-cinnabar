$(document).on("mozreview_ready", function() {
  // TODO: Stop hardcoding endpoint urls and provide them in a template.
  var TRY_AUTOLAND_URL   = "/api/extensions/mozreview.extension.MozReviewExtension/try-autoland-triggers/";
  var AUTOLAND_URL       = "/api/extensions/mozreview.extension.MozReviewExtension/autoland-triggers/";
  var COMMIT_REWRITE_URL = "/api/extensions/mozreview.extension.MozReviewExtension/commit_rewrite/";

  var try_trigger = $("#autoland-try-trigger");
  var autoland_trigger = $("#autoland-trigger");
  var trychooser_url = $('#repository').data('trychooser-url') || "";

  function show_error(error_text) {
    $("#activity-indicator")
      .addClass("error")
      .text('')
      .append(
        $('<div/>').text(gettext('An error occurred:'))
      )
      .append(
        $('<div/>').addClass("error_msg").text(error_text)
      )
      .append(
        $('<div/>')
        .append(
          $("<a/>")
            .text(gettext("Dismiss"))
            .attr("href", "#")
            .click(function(event) {
              event.preventDefault();
              $("#activity-indicator").fadeOut("fast");
            })
        )
      )
      .show();
  }

  if (!MozReview.autolandingToTryEnabled) {
    try_trigger.attr('title', 'Try builds cannot be triggered for this repository');
  } else if ($("#draft-banner").is(":visible")) {
    try_trigger.attr('title', 'Try builds cannot be triggered on draft review requests');
  } else if (!MozReview.hasScmLevel1) {
    try_trigger.attr('title', 'You do not have the required scm level to trigger a try build');
  } else if (!MozReview.reviewRequestPending) {
    try_trigger.attr('title', 'You can not trigger a try build on a closed review request');
  } else {
    try_trigger.css('opacity', '1');

    $("#autoland-try-trigger").click(function() {
      var box = $("<div/>")
          .addClass("formdlg")
          .keypress(function(e) {
              e.stopPropagation();
          });

      box.width("60em");
      var html = [
        '<label for="mozreview-autoland-try-syntax">TryChooser Syntax</label>',
        '<textarea id="mozreview-autoland-try-syntax" name="mozreview-autoland-try-syntax" placeholder="try: -b do -p win32 -u all -t none"/>',
        '<p>Enter TryChooser syntax here for your Try build.</p>',
        '<p><span id="try-syntax-error">You have an error in your try syntax</span></p>',
        '<p><details id="mozreview-open-try"><summary>Graphically build syntax</summary><p><iframe id="mozreview-trychooser-iframe" src="' + trychooser_url + '"/></p></details></p>',
      ];


      var oldTrySyntax = $('#ci-actions').data('try-syntax');

      if (oldTrySyntax !== undefined) {
        box.find('#mozreview-autoland-try-syntax').val(oldTrySyntax);
      }

      box.find('#mozreview-autoland-try-syntax').on('change', function () {allowReset = true});

      // in case there was no previous try push, allow the trychooser to reset
      // the try syntax. Else, wait for it to be changed
      var allowReset = oldTrySyntax === undefined || oldTrySyntax === "";
      if (trychooser_url === "") {
        html.pop();
      } else {
        window.onmessage = function(e) {
          if (e.origin === new URL(trychooser_url).origin) {
            if (e.data != "") {
              $('#mozreview-autoland-try-syntax').val("try: " + e.data)
            } else if (allowReset) {
              // We only reset the value if it has been changed since
              // This prevents the first load of the page from overriding
              // the try syntax input
              $('#mozreview-autoland-try-syntax').val("")
            }
          }
        }
      }

      for (var i = 0; i < html.length; ++i) {
        box.append($(html[i]).addClass("mozreview-autoland-try-chooser-element"));
      }



      box.modalBox({
          title: "Trigger a Try Build",
          buttons: [
            $('<input type="button"/>')
                .val(gettext("Cancel")),
            $('<input type="button"/>')
              .val("Submit")
              .click(function() {
                var submit = $(this);
                var tryInput = $("#mozreview-autoland-try-syntax");
                var trySyntax = $("#mozreview-autoland-try-syntax").val();

                if (trySyntax.indexOf('try: ') !== 0) {
                  $('#try-syntax-error').css('display', 'block');
                  return false;
                } else {
                  $('#try-syntax-error').css('display', 'none');
                  submit.enable(false);
                  tryInput.enable(false);

                  var activityIndicator = $("#activity-indicator")
                    .removeClass("error")
                    .text(gettext("Scheduling jobs..."))
                    .show();

                  $.ajax({
                    type: "POST",
                    url: TRY_AUTOLAND_URL,
                    data: {
                      review_request_id: MozReview.parentReviewRequest.id,
                      try_syntax: trySyntax
                    }
                  })
                  .done(function() {
                    // There may be a better way to get the review request updates
                    // but this is probably good enough for now
                    window.location.reload();
                  })
                  .fail(function(jqXHR, textStatus, errorThrown) {
                    submit.enable(true);
                    tryInput.enable(true);
                    try {
                      show_error(jQuery.parseJSON(jqXHR.responseText).err.msg);
                    } catch (e) {
                      show_error(jqXHR.responseText);
                    }
                  });
                  return false;
                }
              })
          ]
      });

      $("#mozreview-autoland-try-syntax").focus();

      return false;
    });
  }

  var autoland_confirm = function() {
    var activityIndicator = $("#activity-indicator")
      .removeClass("error")
      .text(gettext("Loading commits..."))
      .show();

    // confirmation UI
    var box = $("<div/>")
      .addClass("formdlg")
      .keypress(function(e) {
        e.stopPropagation();
      })
      .width('60em')
      .append(
        $('<div id="autoland-content"/>)')
          .append('<i>Loading commits...</i>'))
      .modalBox({
        title: "Land Commits",
        buttons: [
          $('<input type="button" id="autoland-cancel"/>')
            .val(gettext("Cancel")),
          $('<input type="button" id="autoland-submit" disabled/>')
            .val("Land")
            .click(send_to_autoland)
        ]
      });

    // fetch rewritten commit messages
    RB.apiCall({
      type: 'GET',
      url: COMMIT_REWRITE_URL + MozReview.parentReviewRequest.id + '/',
      success: function(rsp) {
        // build confirmation dialog
        activityIndicator.hide();
        $('#autoland-content')
          .html(
            '<p>About to land the following commits to <span id="autoland-repo">?</span>.<br>' +
            'Please confirm these commit descriptions are correct before landing.<br>' +
            'If corrections are required please <b>amend</b> the commit ' +
            'message and try again.</p>' +
            '<div id="autoland-commits"/>'
          );
        $('#autoland-repo').text(MozReview.landingRepository);
        var commit_descriptions = {};
        $.each(rsp.commits, function() {
          $('#autoland-commits')
            .append(
              $('<div/>')
                .addClass('autoland-commit-desc')
                .text(this.summary.split("\n")[0]))
            .append(
              $('<div/>')
                .append(
                  $('<span/>')
                    .addClass('autoland-commit-rev')
                    .text(this.commit.substr(0, 12)))
                .append(
                  $('<span/>')
                    .addClass('autoland-commit-reviewers')
                    .text('reviewers: ' + this.reviewers.join(', '))));
          if (this.shipit_carryforward === true) {
            $('#autoland-commits')
              .append(
                $('<span/>')
                  .addClass('rb-icon rb-icon-warning'))
              .append(
                $('<span/>')
                  .addClass('autoland-commit-warning')
                  .text('Warning: commit has changed since review was granted'));
          }
          // store commit descriptions in a form ready to pass to autoland
          commit_descriptions[this.commit.substr(0, 12)] = this.summary;
        });
        $('#autoland-submit')
          .data('commits', commit_descriptions)
          .prop('disabled', false);
      },
      error: function(res) {
        box.modalBox('destroy');
        try {
          show_error(jQuery.parseJSON(res.responseText).err.msg);
        } catch (e) {
          show_error(res.responseText);
        }
      }
    });
  };

  function send_to_autoland() {
    autoland_trigger.off("click", autoland_confirm);

    activityIndicator = $("#activity-indicator")
      .removeClass("error")
      .text(gettext("Triggering landing..."))
      .show();

    $.ajax({
      type: "POST",
      url: AUTOLAND_URL,
      data: {
          review_request_id: MozReview.parentReviewRequest.id,
          commit_descriptions: JSON.stringify(
              $('#autoland-submit').data('commits'))
      }
    })
    .done(function() {
      // There may be a better way to get the review request updates
      // but this is probably good enough for now
      window.location.reload();
    })
    .fail(function(jqXHR, textStatus, errorThrown) {
      var error_text = "";

      try {
        error_text = jQuery.parseJSON(jqXHR.responseText).err.msg;
      } catch (e) {
        error_text = jqXHR.responseText;
      }

      activityIndicator.addClass("error")
        .text(gettext("A server error occurred: " + error_text))
        .append(
          $("<a/>")
            .text(gettext("Dismiss"))
            .attr("href", "#")
            .click(function() {
              activityIndicator.fadeOut("fast");
              return false;
            })
        );

      // Add the handler back in case it was an intermittent
      // failure and we want to allow a retry.
      autoland_trigger.click(autoland_confirm);
    });
  }

  if (!MozReview.autolandingEnabled) {
    autoland_trigger.attr('title', 'Landing is not supported for this repository');
  } else if ($("#draft-banner").is(":visible")) {
    autoland_trigger.attr('title', 'Draft review requests cannot be landed');
  } else if (!MozReview.hasScmLevel3) {
    autoland_trigger.attr('title', 'You must have scm_level_3 access to land');
  } else if (!MozReview.reviewRequestPending) {
    autoland_trigger.attr('title', 'You can not autoland from a closed review request');
  } else {
    MozReview.parentReviewRequest.ready({
      error: function() {
        autoland_trigger.attr('title', 'Error determining approval');
      },
      ready: function() {
        $(document).trigger('mr:update_autoland_menuitem');
      }
    });
  }

  $(document).on('mr:update_autoland_menuitem', function() {
    var $autoland_trigger = $('#autoland-trigger');
    if (!MozReview.parentReviewRequest.get('approved')) {
      $autoland_trigger
        .attr('title', 'Review request not approved for landing: ' +
          MozReview.parentReviewRequest.get('approvalFailure'))
        .css('opacity', 0.5)
        .off('click', autoland_confirm);
    } else {
      $autoland_trigger
        .attr('title', '')
        .css('opacity', 1)
        .on('click', autoland_confirm);
    }
  });

  $('.action-landed[data-repository][data-revision]').each(function(index, elem) {
    var repository = $(elem).data('repository');
    var revision = $(elem).data('revision');
    var actionHeading = $(elem).find('.action-info > .action-heading')[0];
    var actionMeta = $(elem).find('.action-info > .action-meta')[0];
    $.ajax({
      url: 'https://treeherder.mozilla.org/api/project/'+repository+'/resultset/?revision='+revision,
    })
    .done(function(response) {
      if (response.results.length === 0) {
        $(actionHeading).text('Waiting for revision to appear in Treeherder');
        $(elem).addClass('action-pending');
      } else if (response.results.length > 1) {
        $(actionHeading).text('Error fetching the results for '+revision+' on '+repository+' from Treeherder');
        $(actionMeta).text('Too many results found');
      } else {
        var resultset = response.results[0];
        $.ajax({
          url: 'https://treeherder.mozilla.org/api/project/'+repository+'/resultset/'+resultset.id+'/status/'
        }).done(function(status) {
          if (status.testfailed || status.busted || status.exception) {
            $(actionHeading).text('Some jobs failed on ' + repository);
            $(elem).addClass('action-failure');
          } else {
            if (status.pending || status.running) {
              $(actionHeading).text('Some jobs are still in progress on ' + repository);
              $(elem).addClass('action-pending');
            } else {
              $(actionHeading).text('All the jobs passed on ' + repository);
              $(elem).addClass('action-success');
            }
          }
          var actionMetaText = $.map(status, function(num, s) {
            return num+' jobs '+s;
          });
          $(actionMeta).text(actionMetaText.join());
        });
      }
        $(this).addClass("done");
    });

  });
});
